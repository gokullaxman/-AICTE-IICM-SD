import os
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.core.database import db, now_ist, today_str, timestamp_str
from app.core.security import get_current_user, check_bureau_permission
from app.schemas.payloads import (
    CaseCreateRequest,
    WorkflowUpdateRequest,
    CaseTransferRequest,
    CaseEscalateRequest,
)
from app.services.overlap_engine import CrossBureauOverlapEngine
from app.services.sla_and_alerts import (
    compute_expected_disposal_date,
    evaluate_case_sla,
    get_sla_config_map,
)

router = APIRouter()
engine = CrossBureauOverlapEngine()

def _enrich_case(c: Dict[str, Any], inst_map: Dict[str, Dict[str, Any]], sla_map: Dict[str, Dict[str, Any]], all_links: List[Dict[str, Any]]) -> Dict[str, Any]:
    inst = inst_map.get(c["pid"], {})
    sla = evaluate_case_sla(c, sla_map)
    history_rows = db.fetch_all("SELECT * FROM workflow_history WHERE case_id = ? ORDER BY history_id ASC", (c["case_id"],))
    doc_rows = db.fetch_all("SELECT * FROM uploaded_documents WHERE case_id = ? ORDER BY uploaded_at DESC", (c["case_id"],))
    linked_ids = [
        (lnk["target_case_id"] if lnk["source_case_id"] == c["case_id"] else lnk["source_case_id"])
        for lnk in all_links
        if lnk["is_active"] == 1 and (lnk["source_case_id"] == c["case_id"] or lnk["target_case_id"] == c["case_id"])
    ]
    return {
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
    page_size: int = 20
):
    inst_rows = db.fetch_all("SELECT * FROM institutions")
    inst_map = {i["pid"]: i for i in inst_rows}
    sla_map = get_sla_config_map()
    all_links = db.fetch_all("SELECT * FROM case_links WHERE is_active = 1")
    raw_cases = db.fetch_all("SELECT * FROM complaint_cases ORDER BY receipt_date DESC, case_id DESC")

    # Pre-compute active bureaus per institution for overlap filtering
    active_bureaus_by_pid: Dict[str, set] = {}
    for rc in raw_cases:
        if rc["status"] != "Disposed":
            active_bureaus_by_pid.setdefault(rc["pid"], set()).add(rc["bureau"])

    enriched = [_enrich_case(c, inst_map, sla_map, all_links) for c in raw_cases]

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
            if pid.strip().lower() not in item["pid"].lower():
                continue

        if case_id and case_id.strip():
            if case_id.strip().lower() not in item["case_id"].lower():
                continue

        if bureau and bureau != "ALL":
            if item["bureau"] != bureau:
                continue

        if region and region != "ALL":
            if item["region"].lower() != region.lower():
                continue

        if category and category != "ALL":
            if item["category"] != category:
                continue

        if status and status != "ALL":
            if item["status"] != status:
                continue

        if special_flag and special_flag != "ALL":
            if special_flag == "OVERLAP_ONLY" and not has_overlap:
                continue
            if special_flag == "UNRESOLVED_OVERLAP" and (not has_overlap or is_coordinated):
                continue
            if special_flag == "SLA_BREACHED" and item["sla"]["state"] != "BREACHED":
                continue
            if special_flag == "SLA_APPROACHING" and item["sla"]["state"] != "APPROACHING":
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
def get_case_detail(case_id: str):
    raw = db.fetch_one("SELECT * FROM complaint_cases WHERE case_id = ?", (case_id.strip(),))
    if not raw:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")
    inst_rows = db.fetch_all("SELECT * FROM institutions")
    inst_map = {i["pid"]: i for i in inst_rows}
    sla_map = get_sla_config_map()
    all_links = db.fetch_all("SELECT * FROM case_links WHERE is_active = 1")
    enriched = _enrich_case(raw, inst_map, sla_map, all_links)

    # Include overlap analysis with other active cases of the same institution
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
            last_action_date, expected_disposal_date, match_method, match_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Under Process', ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            entity_res["confidence"]
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
    return get_case_detail(case_id.strip())

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
    return get_case_detail(case_id.strip())

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

    db.execute(
        """UPDATE complaint_cases
           SET status = 'Escalated',
               last_action = ?, next_action = ?, last_action_date = ?
           WHERE case_id = ?""",
        (
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
    return get_case_detail(case_id.strip())

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
