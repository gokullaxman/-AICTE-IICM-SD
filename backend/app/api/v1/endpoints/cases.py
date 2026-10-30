import os
import uuid
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.core.database import db, now_ist, today_str, timestamp_str
from app.core.security import (
    get_current_user,
    get_optional_current_user,
    check_bureau_permission,
    apply_pii_masking
)
from app.schemas.payloads import (
    CaseCreateRequest,
    WorkflowUpdateRequest,
    CaseTransferRequest,
    CaseEscalateRequest,
    ExternalComplaintWebhookRequest,
    PIIUnmaskRequest
)
from app.services.overlap_engine import (
    CrossBureauOverlapEngine,
    cross_lingual_semantic_expansion,
    detect_language
)
from app.services.sla_and_alerts import (
    compute_expected_disposal_date,
    evaluate_case_sla,
    get_sla_config_map,
    run_auto_escalation_ladder
)
from app.services.notification_service import broadcast_alert_notifications

router = APIRouter()
engine = CrossBureauOverlapEngine()

def _enrich_case(
    c: Dict[str, Any],
    inst_map: Dict[str, Dict[str, Any]],
    sla_map: Dict[str, Dict[str, Any]],
    all_links: List[Dict[str, Any]],
    current_user: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    inst = inst_map.get(c["pid"], {})
    sla = evaluate_case_sla(c, sla_map)
    history_rows = db.fetch_all("SELECT * FROM workflow_history WHERE case_id = ? ORDER BY history_id ASC", (c["case_id"],))
    doc_rows = db.fetch_all("SELECT * FROM uploaded_documents WHERE case_id = ? ORDER BY uploaded_at DESC", (c["case_id"],))
    linked_ids = [
        (lnk["target_case_id"] if lnk["source_case_id"] == c["case_id"] else lnk["source_case_id"])
        for lnk in all_links
        if lnk["is_active"] == 1 and (lnk["source_case_id"] == c["case_id"] or lnk["target_case_id"] == c["case_id"])
    ]

    is_action_held = bool(c.get("action_hold", 0) or inst.get("action_hold", 0))
    lead_bureau = inst.get("lead_bureau")

    base = {
        **c,
        "caseId": c["case_id"],
        "instVariantLogged": c["inst_variant_logged"],
        "institutionName": inst.get("name", c["inst_variant_logged"]),
        "region": inst.get("region", ""),
        "city": inst.get("city", ""),
        "state": inst.get("state", ""),
        "receiptDate": c["receipt_date"],
        "officer": c["assigned_officer"],
        "lastAction": c["last_action"],
        "nextAction": c["next_action"],
        "lastActionDate": c["last_action_date"],
        "expectedDisposalDate": c["expected_disposal_date"],
        "matchMethod": c["match_method"],
        "matchConfidence": c["match_confidence"],
        "escalationLevel": int(c.get("escalation_level") or 0),
        "escalationStatus": c.get("escalation_status", "NORMAL"),
        "lastEscalatedAt": c.get("last_escalated_at"),
        "actionHold": is_action_held,
        "leadBureau": lead_bureau,
        "sourcePortal": c.get("source_portal", "MANUAL"),
        "externalRefId": c.get("external_ref_id"),
        "language": c.get("language", "en"),
        "linkedCases": linked_ids,
        "sla": sla,
        "history": [
            {
                "id": h["history_id"],
                "date": h["action_timestamp"],
                "action_type": h["action_type"],
                "from_bureau": h["from_bureau"],
                "bureau": h["to_bureau"],
                "officer": h["officer"],
                "action": h["action_remarks"],
                "doc": h["supporting_doc"]
            }
            for h in history_rows
        ],
        "documents": doc_rows
    }

    # Apply DPDP Act 2023 Complainant PII Masking
    return apply_pii_masking(base, current_user)

@router.get("/")
def search_and_filter_cases(
    q: Optional[str] = None,
    institution_name: Optional[str] = None,
    pid: Optional[str] = None,
    case_id: Optional[str] = None,
    bureau: Optional[str] = None,
    region: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    special_flag: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    current_user: Optional[Dict[str, Any]] = Depends(get_optional_current_user)
):
    inst_rows = db.fetch_all("SELECT * FROM institutions")
    inst_map = {i["pid"]: i for i in inst_rows}
    sla_map = get_sla_config_map()
    all_links = db.fetch_all("SELECT * FROM case_links WHERE is_active = 1")
    raw_cases = db.fetch_all("SELECT * FROM complaint_cases ORDER BY receipt_date DESC, case_id DESC")

    active_bureaus_by_pid: Dict[str, set] = {}
    for rc in raw_cases:
        if rc["status"] != "Disposed":
            active_bureaus_by_pid.setdefault(rc["pid"], set()).add(rc["bureau"])

    enriched = [_enrich_case(c, inst_map, sla_map, all_links, current_user) for c in raw_cases]

    filtered = []
    for item in enriched:
        inst = inst_map.get(item["pid"], {})
        active_b = active_bureaus_by_pid.get(item["pid"], set())
        has_overlap = len(active_b) > 1 and item["status"] != "Disposed"
        is_coordinated = inst.get("coordination_status") == "COORDINATED"

        if q and q.strip():
            ql = q.strip().lower()
            hay = " ".join([
                item["case_id"], item["pid"], item["inst_variant_logged"],
                item["institutionName"], item["category"], item["assigned_officer"],
                item["complainant"], item.get("description", ""), inst.get("aliases", "")
            ]).lower()
            if ql not in hay:
                continue

        if institution_name and institution_name.strip():
            inl = institution_name.strip().lower()
            if inl not in item["institutionName"].lower() and inl not in item["inst_variant_logged"].lower() and inl not in inst.get("aliases", "").lower():
                continue

        if pid and pid.strip():
            if item["pid"].lower() != pid.strip().lower():
                continue

        if case_id and case_id.strip():
            if case_id.strip().lower() not in item["case_id"].lower():
                continue

        if bureau and bureau != "ALL":
            if item["bureau"].upper() != bureau.strip().upper():
                continue

        if region and region != "ALL":
            if item["region"].lower() != region.strip().lower():
                continue

        if category and category != "ALL":
            if item["category"].strip().lower() != category.strip().lower():
                continue

        if status and status != "ALL":
            if item["status"].lower() != status.strip().lower():
                continue

        if special_flag and special_flag != "ALL":
            if special_flag == "OVERLAPPING" and not (has_overlap and not is_coordinated):
                continue
            if special_flag == "COORDINATED" and not (has_overlap and is_coordinated):
                continue
            if special_flag == "SLA_BREACHED" and item["sla"]["state"] != "BREACHED":
                continue
            if special_flag == "SLA_APPROACHING" and item["sla"]["state"] != "APPROACHING":
                continue
            if special_flag == "ACTION_HOLD" and not item["actionHold"]:
                continue
            if special_flag == "ESCALATED" and item["escalationLevel"] == 0:
                continue

        if date_from and date_from.strip():
            if item["receipt_date"] < date_from.strip():
                continue

        if date_to and date_to.strip():
            if item["receipt_date"] > date_to.strip():
                continue

        filtered.append(item)

    total = len(filtered)
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    start = (page - 1) * page_size
    paged = filtered[start : start + page_size]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "items": paged
    }

@router.get("/{case_id}")
def get_case_detail(
    case_id: str,
    current_user: Optional[Dict[str, Any]] = Depends(get_optional_current_user)
):
    raw = db.fetch_one("SELECT * FROM complaint_cases WHERE case_id = ?", (case_id.strip(),))
    if not raw:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")
    inst_rows = db.fetch_all("SELECT * FROM institutions")
    inst_map = {i["pid"]: i for i in inst_rows}
    sla_map = get_sla_config_map()
    all_links = db.fetch_all("SELECT * FROM case_links WHERE is_active = 1")
    enriched = _enrich_case(raw, inst_map, sla_map, all_links, current_user)

    other_active = [
        c for c in db.fetch_all("SELECT * FROM complaint_cases WHERE pid = ? AND case_id != ? AND status != 'Disposed'", (raw["pid"], raw["case_id"]))
    ]
    overlap_comparisons = [
        {
            "case_id": oc["case_id"],
            "bureau": oc["bureau"],
            "category": oc["category"],
            "status": oc["status"],
            "assigned_officer": oc["assigned_officer"],
            "metrics": engine.evaluate_case_pair_content_overlap(raw, oc, raw.get("match_confidence", 100.0))
        }
        for oc in other_active
    ]
    return {
        "case": enriched,
        "institution": inst_map.get(raw["pid"]),
        "overlap_comparisons": overlap_comparisons
    }

@router.post("/", status_code=status.HTTP_201_CREATED)
def register_new_complaint(
    payload: CaseCreateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    check_bureau_permission(current_user, payload.bureau, allow_cross_bureau=True)
    institutions = db.fetch_all("SELECT * FROM institutions")

    entity_res = engine.resolve_institution_entity(
        raw_name=payload.institution_name,
        raw_pid=payload.pid,
        institutions=institutions
    )
    if not entity_res["matched"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Could not resolve '{payload.institution_name}' to an approved AICTE Institution "
                f"(Confidence {entity_res['confidence']}% < {entity_res['threshold']}% threshold). "
                f"Please provide a valid AICTE Permanent ID (1-XXXXXXX) or verified institution name."
            )
        )

    matched_inst = entity_res["institution"]
    resolved_pid = matched_inst["pid"]
    curr_date = today_str(0)
    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)
    exp_date = compute_expected_disposal_date(curr_date, payload.category, payload.sla_days or 0)

    # Detect language of complaint description
    lang = detect_language(payload.description)

    # Generate unique sequential Case ID
    existing_cnt = db.fetch_one("SELECT COUNT(*) AS cnt FROM complaint_cases")["cnt"]
    new_case_id = f"AICTE-{payload.bureau}-{910 + existing_cnt + 1:04d}"

    active_cases = db.fetch_all("SELECT * FROM complaint_cases WHERE pid = ? AND status != 'Disposed'", (resolved_pid,))
    overlap_eval = engine.check_cross_bureau_overlap(
        pid=resolved_pid,
        receiving_bureau=payload.bureau,
        active_cases=active_cases,
        incoming_category=payload.category,
        incoming_description=payload.description,
        incoming_date=curr_date,
        entity_confidence=entity_res["confidence"]
    )

    db.execute(
        """INSERT INTO complaint_cases (
            case_id, pid, inst_variant_logged, receipt_date, category, complainant,
            description, status, bureau, assigned_officer, last_action, next_action,
            last_action_date, expected_disposal_date, match_method, match_confidence,
            escalation_level, escalation_status, complainant_phone, complainant_email,
            source_portal, language
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Under Process', ?, ?, ?, ?, ?, ?, ?, ?, 0, 'NORMAL', ?, ?, 'MANUAL', ?)""",
        (
            new_case_id,
            resolved_pid,
            payload.institution_name.strip(),
            curr_date,
            payload.category.strip(),
            payload.complainant.strip(),
            payload.description.strip(),
            payload.bureau,
            payload.assigned_officer.strip(),
            f"{curr_date}: Complaint registered — {payload.description.strip()[:140]}",
            payload.next_action.strip(),
            curr_date,
            exp_date,
            entity_res["method"],
            entity_res["confidence"],
            payload.complainant_phone.strip() if payload.complainant_phone else None,
            payload.complainant_email.strip() if payload.complainant_email else None,
            lang
        )
    )

    db.execute(
        """INSERT INTO workflow_history (
            case_id, action_timestamp, action_type, from_bureau, to_bureau,
            officer, action_remarks, supporting_doc
        ) VALUES (?, ?, 'INTAKE', NULL, ?, ?, ?, ?)""",
        (
            new_case_id,
            curr_ts,
            payload.bureau,
            payload.assigned_officer.strip(),
            f"Complaint registered: {payload.description.strip()}",
            f"Intake_{new_case_id}.pdf"
        )
    )

    # Auto-link colliding cross-bureau cases
    for col in overlap_eval["colliding_cases"]:
        lnk_id = f"LNK-{uuid.uuid4().hex[:8].upper()}"
        sim_score = col["overlap_metrics"]["composite_overlap_score"]
        db.execute(
            """INSERT INTO case_links (
                link_id, source_case_id, target_case_id, pid, link_type, similarity_score,
                lead_bureau, coordination_directive, linked_by, linked_at, is_active
            ) VALUES (?, ?, ?, ?, 'AI_FUZZY_CONTENT', ?, ?, ?, ?, ?, 1)""",
            (
                lnk_id,
                new_case_id,
                col["case_id"],
                resolved_pid,
                sim_score,
                payload.bureau,
                f"Auto-linked on intake (Composite Overlap Score: {sim_score}%)",
                current_user["email"],
                curr_ts
            )
        )

    if overlap_eval["has_overlap"]:
        bureaus_str = ", ".join(overlap_eval["bureaus_involved"])
        db.execute(
            "UPDATE institutions SET coordination_status = 'UNRESOLVED', coordination_note = ? WHERE pid = ?",
            (f"Potential Overlapping Cases – Coordination Required across {bureaus_str}", resolved_pid)
        )

    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="CASE_REGISTERED",
        target_entity=new_case_id,
        details=f"Registered case {new_case_id} for {matched_inst['name']} ({resolved_pid}). Overlap={overlap_eval['has_overlap']}"
    )

    return {
        "status": "created",
        "case_id": new_case_id,
        "resolved_pid": resolved_pid,
        "entity_resolution": entity_res,
        "overlap_evaluation": overlap_eval
    }

# ============================================================================
# Feature 3: Complaint Intake API & Webhook (Single Window Integration)
# ============================================================================

@router.post("/webhook", status_code=status.HTTP_201_CREATED)
@router.post("/intake", status_code=status.HTTP_201_CREATED)
def external_complaint_intake_webhook(
    payload: ExternalComplaintWebhookRequest,
    x_webhook_secret: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None)
):
    """
    Single-Window Automated Complaint Intake Webhook API for external grievance feeds
    (CPGRAMS, AICTE Citizen Portal, State DTEs). Supports multilingual intake (Hindi, Tamil),
    instant entity resolution, real-time overlap detection, and auto-linking.
    """
    # Verify Webhook Secret or Bearer Token
    authenticated = False
    caller_identity = "CPGRAMS_FEED_INGEST"
    if x_webhook_secret and x_webhook_secret.strip() == settings.WEBHOOK_SECRET:
        authenticated = True
        caller_identity = f"WEBHOOK_{payload.source_portal.upper()}"
    elif authorization:
        user = get_optional_current_user(authorization)
        if user:
            authenticated = True
            caller_identity = user.get("email", "AUTHORIZED_USER")

    if not authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized Webhook Access: Invalid X-Webhook-Secret or Bearer Token."
        )

    institutions = db.fetch_all("SELECT * FROM institutions")
    entity_res = engine.resolve_institution_entity(
        raw_name=payload.institution_name,
        raw_pid=payload.aicte_pid,
        institutions=institutions
    )
    if not entity_res["matched"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Webhook Ingestion Failed: Could not resolve '{payload.institution_name}' to an approved AICTE Institution."
        )

    matched_inst = entity_res["institution"]
    resolved_pid = matched_inst["pid"]

    # Multilingual expansion and automatic category detection
    expanded_text, detected_lang, predicted_cat = cross_lingual_semantic_expansion(payload.description)
    category = payload.category or predicted_cat or "Fee Refund & Overcharging"

    # Auto-assign initial bureau based on regulatory category
    category_to_bureau = {
        "Hostel & Campus Safety / Ragging": "PGRC",
        "Fee Refund & Overcharging": "PGRC",
        "Original Certificate Withholding": "PGRC",
        "Unapproved Intake / Excess Admission": "RB",
        "Faculty Salary & Qualification Norms": "RB",
        "Fake Faculty / Biometric Attendance": "VC",
        "Infrastructure & Lab Deficit": "AB"
    }
    assigned_bureau = category_to_bureau.get(category, "PGRC")

    # Assigned Officer designation
    bureau_officers = {
        "PGRC": "Shri V. K. Sharma (PGRC Scrutiny Cell)",
        "RB": "Dr. Ananya Nair (Regulation Desk)",
        "AB": "Shri M. S. Gill (Approval Directorate)",
        "VC": "Shri D. P. Singh (Vigilance Ingestion)",
        "RO": "Regional Officer Directorate"
    }
    assigned_officer = bureau_officers.get(assigned_bureau, "Desk Officer (Central Scrutiny)")

    curr_date = payload.receipt_date or today_str(0)
    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)
    exp_date = compute_expected_disposal_date(curr_date, category)

    existing_cnt = db.fetch_one("SELECT COUNT(*) AS cnt FROM complaint_cases")["cnt"]
    new_case_id = f"AICTE-{assigned_bureau}-{910 + existing_cnt + 1:04d}"

    active_cases = db.fetch_all("SELECT * FROM complaint_cases WHERE pid = ? AND status != 'Disposed'", (resolved_pid,))
    overlap_eval = engine.check_cross_bureau_overlap(
        pid=resolved_pid,
        receiving_bureau=assigned_bureau,
        active_cases=active_cases,
        incoming_category=category,
        incoming_description=payload.description,
        incoming_date=curr_date,
        entity_confidence=entity_res["confidence"]
    )

    db.execute(
        """INSERT INTO complaint_cases (
            case_id, pid, inst_variant_logged, receipt_date, category, complainant,
            description, status, bureau, assigned_officer, last_action, next_action,
            last_action_date, expected_disposal_date, match_method, match_confidence,
            escalation_level, escalation_status, complainant_phone, complainant_email,
            external_ref_id, source_portal, language
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Under Process', ?, ?, ?, ?, ?, ?, ?, ?, 0, 'NORMAL', ?, ?, ?, ?, ?)""",
        (
            new_case_id,
            resolved_pid,
            payload.institution_name.strip(),
            curr_date,
            category,
            payload.complainant_name.strip(),
            payload.description.strip(),
            assigned_bureau,
            assigned_officer,
            f"{curr_date}: Ingested via {payload.source_portal} Single-Window Webhook (Ref: {payload.external_reference_id})",
            f"Conduct initial scrutiny and correlate with active overlap cluster.",
            curr_date,
            exp_date,
            f"Webhook Resolution ({entity_res['method']})",
            entity_res["confidence"],
            payload.complainant_phone.strip() if payload.complainant_phone else None,
            payload.complainant_email.strip() if payload.complainant_email else None,
            payload.external_reference_id.strip(),
            payload.source_portal.strip(),
            detected_lang
        )
    )

    db.execute(
        """INSERT INTO workflow_history (
            case_id, action_timestamp, action_type, from_bureau, to_bureau,
            officer, action_remarks, supporting_doc
        ) VALUES (?, ?, 'INTAKE', NULL, ?, ?, ?, ?)""",
        (
            new_case_id,
            curr_ts,
            assigned_bureau,
            f"{payload.source_portal} Webhook Gateway",
            f"External grievance intake processed from {payload.source_portal}. External Ref: {payload.external_reference_id}.",
            f"Webhook_Receipt_{new_case_id}.pdf"
        )
    )

    # Auto-link colliding cases if overlap exists
    for col in overlap_eval["colliding_cases"]:
        lnk_id = f"LNK-{uuid.uuid4().hex[:8].upper()}"
        sim_score = col["overlap_metrics"]["composite_overlap_score"]
        db.execute(
            """INSERT INTO case_links (
                link_id, source_case_id, target_case_id, pid, link_type, similarity_score,
                lead_bureau, coordination_directive, linked_by, linked_at, is_active
            ) VALUES (?, ?, ?, ?, 'AI_FUZZY_CONTENT', ?, ?, ?, ?, ?, 1)""",
            (
                lnk_id,
                new_case_id,
                col["case_id"],
                resolved_pid,
                sim_score,
                assigned_bureau,
                f"Auto-linked on webhook intake (Composite Overlap: {sim_score}%)",
                caller_identity,
                curr_ts
            )
        )

    if overlap_eval["has_overlap"]:
        bureaus_str = ", ".join(overlap_eval["bureaus_involved"])
        db.execute(
            "UPDATE institutions SET coordination_status = 'UNRESOLVED', coordination_note = ? WHERE pid = ?",
            (f"Potential Overlapping Cases – Coordination Required across {bureaus_str}", resolved_pid)
        )

    # Hash-chained audit logging
    db.log_audit(
        actor_email=caller_identity,
        actor_role="EXTERNAL_WEBHOOK_INGEST",
        actor_bureau=assigned_bureau,
        event_type="WEBHOOK_COMPLAINT_INTAKE",
        target_entity=new_case_id,
        details=(
            f"Ingested external grievance from {payload.source_portal} ({payload.external_reference_id}) "
            f"for {matched_inst['name']} ({resolved_pid}). Overlap={overlap_eval['has_overlap']}. Lang={detected_lang}"
        )
    )

    receipt_token = uuid.uuid4().hex.upper()
    return {
        "status": "PROCESSED",
        "case_id": new_case_id,
        "external_reference_id": payload.external_reference_id,
        "source_portal": payload.source_portal,
        "resolved_institution": {
            "pid": resolved_pid,
            "name": matched_inst["name"],
            "city": matched_inst["city"],
            "state": matched_inst["state"]
        },
        "assigned_bureau": assigned_bureau,
        "assigned_officer": assigned_officer,
        "category": category,
        "language_detected": detected_lang,
        "has_overlap": overlap_eval["has_overlap"],
        "overlap_evaluation": overlap_eval,
        "expected_disposal_date": exp_date,
        "intake_receipt_token": receipt_token
    }

# ============================================================================
# Feature 1: Lead-Bureau Designation & Action Hold Enforcement
# ============================================================================

@router.post("/{case_id}/workflow")
def update_case_workflow(
    case_id: str,
    payload: WorkflowUpdateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    c = db.fetch_one("SELECT * FROM complaint_cases WHERE case_id = ?", (case_id.strip(),))
    if not c:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")

    check_bureau_permission(current_user, c["bureau"])

    # Enforce Lead-Bureau Action Hold
    inst = db.fetch_one("SELECT * FROM institutions WHERE pid = ?", (c["pid"],))
    if inst and inst.get("action_hold") == 1 and inst.get("lead_bureau"):
        lead_b = inst["lead_bureau"]
        user_role = current_user.get("role", "")
        user_bureau = current_user.get("bureau_code", "")
        if user_role != "COMPETENT_AUTHORITY" and user_bureau != lead_b and not payload.force_lead_override:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"ACTION HOLD ACTIVE: Institution '{inst.get('name', c['pid'])}' ({c['pid']}) is currently under active coordination "
                    f"led by Lead Bureau '{lead_b}'. Unilateral actions by '{user_bureau}' are placed on HOLD to prevent parallel or conflicting "
                    f"regulatory directives under the AICTE single-window mandate. Coordinate directly through Lead Bureau ({lead_b}) or request HQ clearance."
                )
            )

    curr_date = today_str(0)
    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)

    db.execute(
        """UPDATE complaint_cases
           SET status = ?, last_action = ?, next_action = ?, last_action_date = ?
           WHERE case_id = ?""",
        (
            payload.new_status,
            f"{curr_date}: {payload.remarks.strip()}",
            payload.next_action.strip(),
            curr_date,
            case_id.strip()
        )
    )

    db.execute(
        """INSERT INTO workflow_history (
            case_id, action_timestamp, action_type, from_bureau, to_bureau,
            officer, action_remarks, supporting_doc
        ) VALUES (?, ?, 'STATUS_UPDATE', ?, ?, ?, ?, ?)""",
        (
            case_id.strip(),
            curr_ts,
            c["bureau"],
            c["bureau"],
            f"{current_user['full_name']} ({current_user['bureau_code']})",
            f"[Status -> {payload.new_status}] {payload.remarks.strip()}",
            payload.supporting_doc.strip()
        )
    )

    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="WORKFLOW_STATUS_UPDATE",
        target_entity=case_id.strip(),
        details=f"Updated status of {case_id} to '{payload.new_status}': {payload.remarks.strip()}"
    )
    return get_case_detail(case_id.strip(), current_user)

@router.post("/{case_id}/transfer")
def transfer_case_between_bureaus(
    case_id: str,
    payload: CaseTransferRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    c = db.fetch_one("SELECT * FROM complaint_cases WHERE case_id = ?", (case_id.strip(),))
    if not c:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")

    check_bureau_permission(current_user, c["bureau"])

    # Enforce Lead-Bureau Action Hold
    inst = db.fetch_one("SELECT * FROM institutions WHERE pid = ?", (c["pid"],))
    if inst and inst.get("action_hold") == 1 and inst.get("lead_bureau"):
        lead_b = inst["lead_bureau"]
        user_role = current_user.get("role", "")
        user_bureau = current_user.get("bureau_code", "")
        if user_role != "COMPETENT_AUTHORITY" and user_bureau != lead_b and not payload.force_lead_override:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"ACTION HOLD ACTIVE: Transfer blocked. Institution '{inst.get('name', c['pid'])}' is under coordination led by {lead_b}. "
                    f"Unilateral transfers are restricted while action hold is active."
                )
            )

    from_bureau = c["bureau"]
    curr_date = today_str(0)
    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)

    db.execute(
        """UPDATE complaint_cases
           SET status = 'Transferred', bureau = ?, assigned_officer = ?,
               last_action = ?, next_action = ?, last_action_date = ?
           WHERE case_id = ?""",
        (
            payload.to_bureau,
            payload.target_officer.strip(),
            f"{curr_date}: [TRANSFERRED {from_bureau} -> {payload.to_bureau}] {payload.transfer_reason.strip()}",
            payload.next_action.strip(),
            curr_date,
            case_id.strip()
        )
    )

    db.execute(
        """INSERT INTO workflow_history (
            case_id, action_timestamp, action_type, from_bureau, to_bureau,
            officer, action_remarks, supporting_doc
        ) VALUES (?, ?, 'TRANSFER', ?, ?, ?, ?, ?)""",
        (
            case_id.strip(),
            curr_ts,
            from_bureau,
            payload.to_bureau,
            f"{current_user['full_name']} ({from_bureau} -> {payload.to_bureau})",
            f"Inter-Bureau Transfer from {from_bureau} to {payload.to_bureau} (Assigned to {payload.target_officer}): {payload.transfer_reason.strip()}",
            payload.transfer_order_doc.strip()
        )
    )

    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="CASE_TRANSFERRED",
        target_entity=case_id.strip(),
        details=f"Transferred {case_id} from {from_bureau} to {payload.to_bureau} ({payload.target_officer}). Reason: {payload.transfer_reason}"
    )
    return get_case_detail(case_id.strip(), current_user)

@router.post("/{case_id}/escalate")
def escalate_case(
    case_id: str,
    payload: CaseEscalateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    c = db.fetch_one("SELECT * FROM complaint_cases WHERE case_id = ?", (case_id.strip(),))
    if not c:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")

    check_bureau_permission(current_user, c["bureau"], allow_cross_bureau=True)
    curr_date = today_str(0)
    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)
    new_level = max(1, int(c.get("escalation_level") or 0) + 1)

    db.execute(
        """UPDATE complaint_cases
           SET status = 'Escalated',
               escalation_level = ?,
               escalation_status = ?,
               last_escalated_at = ?,
               last_action = ?, next_action = ?, last_action_date = ?
           WHERE case_id = ?""",
        (
            new_level,
            f"ESCALATED_L{new_level}",
            curr_ts,
            f"{curr_date}: [ESCALATED to {payload.escalation_authority.strip()}] {payload.escalation_reason.strip()}",
            payload.next_action.strip(),
            curr_date,
            case_id.strip()
        )
    )

    db.execute(
        """INSERT INTO workflow_history (
            case_id, action_timestamp, action_type, from_bureau, to_bureau,
            officer, action_remarks, supporting_doc
        ) VALUES (?, ?, 'ESCALATE', ?, ?, ?, ?, ?)""",
        (
            case_id.strip(),
            curr_ts,
            c["bureau"],
            c["bureau"],
            f"{current_user['full_name']} ({current_user['bureau_code']})",
            f"Escalated to {payload.escalation_authority.strip()}: {payload.escalation_reason.strip()}",
            payload.supporting_doc.strip()
        )
    )

    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="CASE_ESCALATED",
        target_entity=case_id.strip(),
        details=f"Escalated {case_id} to {payload.escalation_authority}: {payload.escalation_reason}"
    )
    return get_case_detail(case_id.strip(), current_user)

# ============================================================================
# Feature 2: Auto-Escalation Ladder Trigger
# ============================================================================

@router.post("/auto-escalate")
def trigger_auto_escalation_ladder(
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Triggers the automated SLA escalation ladder scan across all active complaints."""
    promoted = run_auto_escalation_ladder()
    return {
        "status": "COMPLETED",
        "cases_escalated_count": len(promoted),
        "escalated_cases": promoted
    }

# ============================================================================
# Feature 8: DPDP Act Complainant PII Lawful Unmasking API
# ============================================================================

@router.post("/{case_id}/unmask-pii")
def unmask_complainant_pii(
    case_id: str,
    payload: PIIUnmaskRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Lawfully unmasks complainant PII (name, phone, email) under the Digital Personal Data
    Protection (DPDP) Act 2023. Generates an immutable, hash-chained audit record recording
    the officer identity, justification reason, and statutory reference.
    """
    c = db.fetch_one("SELECT * FROM complaint_cases WHERE case_id = ?", (case_id.strip(),))
    if not c:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")

    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)

    # Log to tamper-evident hash-chained audit trail
    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="PII_UNMASK_ACCESSED",
        target_entity=case_id.strip(),
        details=(
            f"Officer accessed unmasked complainant PII for case {case_id.strip()}. "
            f"Justification: {payload.justification_reason.strip()}. Reference: {payload.statutory_reference or 'N/A'}"
        )
    )

    return {
        "case_id": case_id.strip(),
        "is_pii_unmasked": True,
        "complainant": c["complainant"],
        "complainant_email": c.get("complainant_email") or "Not provided",
        "complainant_phone": c.get("complainant_phone") or "Not provided",
        "unmasked_by": current_user["email"],
        "unmasked_at": curr_ts,
        "justification_logged": payload.justification_reason.strip(),
        "statutory_compliance": "DPDP Act 2023 Section 8 (Lawful Processing & Audit Logged)"
    }

@router.post("/{case_id}/documents", status_code=status.HTTP_201_CREATED)
async def upload_supporting_document(
    case_id: str,
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    c = db.fetch_one("SELECT * FROM complaint_cases WHERE case_id = ?", (case_id.strip(),))
    if not c:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")

    allowed_exts = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx", ".txt", ".csv"}
    orig_name = file.filename or "Supporting_Document.pdf"
    ext = os.path.splitext(orig_name)[1].lower()
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file extension '{ext}'. Allowed: {', '.join(sorted(allowed_exts))}"
        )

    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds 10 MB limit")

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    doc_id = f"DOC-{uuid.uuid4().hex[:10].upper()}"
    safe_stored_name = f"{case_id.strip()}_{doc_id}{ext}"
    stored_path = os.path.join(settings.UPLOAD_DIR, safe_stored_name)
    with open(stored_path, "wb") as f_out:
        f_out.write(content)

    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)
    db.execute(
        """INSERT INTO uploaded_documents (
            doc_id, case_id, original_filename, stored_filename,
            mime_type, file_size_bytes, uploaded_by, uploaded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            doc_id,
            case_id.strip(),
            orig_name,
            safe_stored_name,
            file.content_type or "application/octet-stream",
            len(content),
            current_user["email"],
            curr_ts
        )
    )

    db.execute(
        """INSERT INTO workflow_history (
            case_id, action_timestamp, action_type, from_bureau, to_bureau,
            officer, action_remarks, supporting_doc
        ) VALUES (?, ?, 'DOC_UPLOAD', ?, ?, ?, ?, ?)""",
        (
            case_id.strip(),
            curr_ts,
            c["bureau"],
            c["bureau"],
            f"{current_user['full_name']} ({current_user['bureau_code']})",
            f"Uploaded supporting evidence document '{orig_name}' ({len(content)} bytes).",
            orig_name
        )
    )

    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="DOCUMENT_UPLOADED",
        target_entity=case_id.strip(),
        details=f"Uploaded file '{orig_name}' ({len(content)} bytes) as {doc_id}"
    )

    return {
        "doc_id": doc_id,
        "case_id": case_id.strip(),
        "original_filename": orig_name,
        "file_size_bytes": len(content),
        "uploaded_by": current_user["email"],
        "uploaded_at": curr_ts
    }

@router.get("/{case_id}/documents/{doc_id}/download")
def download_supporting_document(case_id: str, doc_id: str):
    doc = db.fetch_one(
        "SELECT * FROM uploaded_documents WHERE case_id = ? AND doc_id = ?",
        (case_id.strip(), doc_id.strip())
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    path = os.path.join(settings.UPLOAD_DIR, doc["stored_filename"])
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Stored file not found on server disk")
    return FileResponse(path, media_type=doc["mime_type"], filename=doc["original_filename"])
