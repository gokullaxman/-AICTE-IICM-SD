import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Any, Dict, List, Optional
from app.core.database import db, now_ist, today_str, timestamp_str
from app.core.security import get_current_user
from app.schemas.payloads import (
    OverlapCheckRequest,
    ManualCaseLinkRequest,
    ManualCaseUnlinkRequest,
    JointCoordinationRequest,
    LeadBureauAssignmentRequest
)
from app.services.overlap_engine import CrossBureauOverlapEngine
from app.services.sla_and_alerts import evaluate_case_sla, get_sla_config_map
from app.services.risk_engine import calculate_institution_risk

router = APIRouter()
engine = CrossBureauOverlapEngine()

@router.get("/groups")
def list_overlap_groups(coordination_filter: str = "ALL"):
    institutions = db.fetch_all("SELECT * FROM institutions")
    sla_map = get_sla_config_map()
    all_cases = db.fetch_all("SELECT * FROM complaint_cases WHERE status != 'Disposed' ORDER BY receipt_date DESC")
    active_links = db.fetch_all("SELECT * FROM case_links WHERE is_active = 1")

    by_pid: Dict[str, List[Dict[str, Any]]] = {}
    for c in all_cases:
        by_pid.setdefault(c["pid"], []).append(c)

    groups = []
    for inst in institutions:
        pid = inst["pid"]
        inst_cases = by_pid.get(pid, [])
        bureaus = sorted(list({c["bureau"] for c in inst_cases}))
        pid_links = [lnk for lnk in active_links if lnk["pid"] == pid]

        if len(bureaus) > 1 or len(pid_links) > 0:
            is_coord = inst.get("coordination_status") == "COORDINATED"
            if coordination_filter == "UNRESOLVED" and is_coord:
                continue
            if coordination_filter == "COORDINATED" and not is_coord:
                continue

            # Compute pairwise content & temporal similarity matrix
            pairwise_scores = []
            for i in range(len(inst_cases)):
                for j in range(i + 1, len(inst_cases)):
                    ca, cb = inst_cases[i], inst_cases[j]
                    metrics = engine.evaluate_case_pair_content_overlap(
                        ca, cb, min(ca.get("match_confidence", 100.0), cb.get("match_confidence", 100.0))
                    )
                    is_pair_linked = any(
                        (l["source_case_id"] == ca["case_id"] and l["target_case_id"] == cb["case_id"]) or
                        (l["source_case_id"] == cb["case_id"] and l["target_case_id"] == ca["case_id"])
                        for l in pid_links
                    )
                    pairwise_scores.append({
                        "case_a": ca["case_id"],
                        "bureau_a": ca["bureau"],
                        "case_b": cb["case_id"],
                        "bureau_b": cb["bureau"],
                        "is_linked": is_pair_linked,
                        **metrics
                    })

            enriched_cases = []
            for c in inst_cases:
                c_links = [
                    (l["target_case_id"] if l["source_case_id"] == c["case_id"] else l["source_case_id"])
                    for l in pid_links
                    if l["source_case_id"] == c["case_id"] or l["target_case_id"] == c["case_id"]
                ]
                enriched_cases.append({
                    **c,
                    "caseId": c["case_id"],
                    "instVariantLogged": c["inst_variant_logged"],
                    "receiptDate": c["receipt_date"],
                    "officer": c["assigned_officer"],
                    "lastAction": c["last_action"],
                    "nextAction": c["next_action"],
                    "lastActionDate": c["last_action_date"],
                    "expectedDisposalDate": c["expected_disposal_date"],
                    "matchMethod": c["match_method"],
                    "escalationLevel": int(c.get("escalation_level") or 0),
                    "actionHold": bool(c.get("action_hold", 0) or inst.get("action_hold", 0)),
                    "linkedCases": c_links,
                    "sla": evaluate_case_sla(c, sla_map)
                })

            aliases_raw = inst.get("aliases", "")
            aliases = [a.strip() for a in aliases_raw.split("|") if a.strip()] if isinstance(aliases_raw, str) else list(aliases_raw or [])
            risk_summary = calculate_institution_risk(pid, all_cases)

            groups.append({
                "pid": pid,
                "institution": {
                    **inst,
                    "aliases": aliases,
                    "coordinationStatus": inst.get("coordination_status", "UNRESOLVED"),
                    "coordinationNote": inst.get("coordination_note", ""),
                    "leadBureau": inst.get("lead_bureau"),
                    "actionHold": bool(inst.get("action_hold", 0)),
                    "riskScore": risk_summary["risk_score"],
                    "riskTier": risk_summary["risk_tier"]
                },
                "bureaus": bureaus,
                "isCoordinated": is_coord,
                "leadBureau": inst.get("lead_bureau"),
                "actionHold": bool(inst.get("action_hold", 0)),
                "banner": (
                    f"Coordinated – Lead Bureau ({inst.get('lead_bureau') or 'Assigned'}) Active"
                    if is_coord
                    else "Potential Overlapping Cases – Coordination Required"
                ),
                "cases": enriched_cases,
                "active_links": pid_links,
                "pairwise_content_analysis": pairwise_scores
            })

    return {"count": len(groups), "groups": groups}

@router.post("/detect")
def detect_overlap_live(payload: OverlapCheckRequest):
    institutions = db.fetch_all("SELECT * FROM institutions")
    entity_res = engine.resolve_institution_entity(
        raw_name=payload.institution_name,
        raw_pid=payload.aicte_pid,
        institutions=institutions
    )
    if not entity_res["matched"]:
        return {
            "matched": False,
            "confidence": entity_res["confidence"],
            "threshold": entity_res["threshold"],
            "method": entity_res["method"],
            "banner": f"No Matching AICTE Institution Found (Confidence {entity_res['confidence']}% < {entity_res['threshold']}%)"
        }

    inst = entity_res["institution"]
    active_cases = db.fetch_all("SELECT * FROM complaint_cases WHERE pid = ? AND status != 'Disposed'", (inst["pid"],))
    overlap_res = engine.check_cross_bureau_overlap(
        pid=inst["pid"],
        receiving_bureau=payload.receiving_bureau,
        active_cases=active_cases,
        incoming_category=payload.category,
        incoming_description=payload.complaint_summary,
        incoming_date=today_str(0),
        entity_confidence=entity_res["confidence"]
    )
    return {
        "matched": True,
        "confidence": entity_res["confidence"],
        "threshold": entity_res["threshold"],
        "method": entity_res["method"],
        "institution": inst,
        **overlap_res
    }

@router.post("/link", status_code=status.HTTP_201_CREATED)
def manual_link_cases(
    payload: ManualCaseLinkRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    if payload.source_case_id.strip() == payload.target_case_id.strip():
        raise HTTPException(status_code=400, detail="Cannot link a case to itself")

    src = db.fetch_one("SELECT * FROM complaint_cases WHERE case_id = ?", (payload.source_case_id.strip(),))
    tgt = db.fetch_one("SELECT * FROM complaint_cases WHERE case_id = ?", (payload.target_case_id.strip(),))
    if not src or not tgt:
        raise HTTPException(status_code=404, detail="One or both Case IDs do not exist")

    existing = db.fetch_one(
        """SELECT * FROM case_links
           WHERE is_active = 1 AND (
             (source_case_id = ? AND target_case_id = ?) OR
             (source_case_id = ? AND target_case_id = ?)
           )""",
        (src["case_id"], tgt["case_id"], tgt["case_id"], src["case_id"])
    )
    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)
    metrics = engine.evaluate_case_pair_content_overlap(src, tgt, 100.0)

    if existing:
        db.execute(
            "UPDATE case_links SET coordination_directive = ?, linked_by = ?, linked_at = ? WHERE link_id = ?",
            (payload.reason.strip(), current_user["email"], curr_ts, existing["link_id"])
        )
        link_id = existing["link_id"]
    else:
        link_id = f"LNK-{uuid.uuid4().hex[:8].upper()}"
        db.execute(
            """INSERT INTO case_links (
                link_id, source_case_id, target_case_id, pid, link_type, similarity_score,
                lead_bureau, coordination_directive, linked_by, linked_at, is_active
            ) VALUES (?, ?, ?, ?, 'MANUAL_OFFICER', ?, ?, ?, ?, ?, 1)""",
            (
                link_id,
                src["case_id"],
                tgt["case_id"],
                src["pid"],
                metrics["composite_overlap_score"],
                payload.lead_bureau or "RB",
                payload.reason.strip(),
                current_user["email"],
                curr_ts
            )
        )

    for c_row, other_id in [(src, tgt["case_id"]), (tgt, src["case_id"])]:
        db.execute(
            """INSERT INTO workflow_history (
                case_id, action_timestamp, action_type, from_bureau, to_bureau,
                officer, action_remarks, supporting_doc
            ) VALUES (?, ?, 'MANUAL_LINK', ?, ?, ?, ?, ?)""",
            (
                c_row["case_id"],
                curr_ts,
                c_row["bureau"],
                c_row["bureau"],
                f"{current_user['full_name']} ({current_user['bureau_code']})",
                f"Manually linked with {other_id}: {payload.reason.strip()}",
                f"Case_Link_{link_id}.pdf"
            )
        )

    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="MANUAL_CASE_LINK",
        target_entity=f"{src['case_id']}<->{tgt['case_id']}",
        details=f"Linked {src['case_id']} and {tgt['case_id']} ({link_id}). Reason: {payload.reason.strip()}"
    )

    return {
        "status": "linked",
        "link_id": link_id,
        "source_case_id": src["case_id"],
        "target_case_id": tgt["case_id"],
        "overlap_metrics": metrics
    }

@router.post("/unlink")
def manual_unlink_cases(
    payload: ManualCaseUnlinkRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    src_id = payload.source_case_id.strip()
    tgt_id = payload.target_case_id.strip()
    existing = db.fetch_one(
        """SELECT * FROM case_links
           WHERE is_active = 1 AND (
             (source_case_id = ? AND target_case_id = ?) OR
             (source_case_id = ? AND target_case_id = ?)
           )""",
        (src_id, tgt_id, tgt_id, src_id)
    )
    if not existing:
        raise HTTPException(status_code=404, detail=f"No active link found between {src_id} and {tgt_id}")

    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)
    db.execute("UPDATE case_links SET is_active = 0 WHERE link_id = ?", (existing["link_id"],))

    src_row = db.fetch_one("SELECT * FROM complaint_cases WHERE case_id = ?", (src_id,))
    tgt_row = db.fetch_one("SELECT * FROM complaint_cases WHERE case_id = ?", (tgt_id,))

    for c_row, other_id in [(src_row, tgt_id), (tgt_row, src_id)]:
        if c_row:
            db.execute(
                """INSERT INTO workflow_history (
                    case_id, action_timestamp, action_type, from_bureau, to_bureau,
                    officer, action_remarks, supporting_doc
                ) VALUES (?, ?, 'MANUAL_UNLINK', ?, ?, ?, ?, ?)""",
                (
                    c_row["case_id"],
                    curr_ts,
                    c_row["bureau"],
                    c_row["bureau"],
                    f"{current_user['full_name']} ({current_user['bureau_code']})",
                    f"Manually unlinked from {other_id}: {payload.reason.strip()}",
                    f"Case_Unlink_{existing['link_id']}.pdf"
                )
            )

    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="MANUAL_CASE_UNLINK",
        target_entity=f"{src_id}<->{tgt_id}",
        details=f"Unlinked {src_id} and {tgt_id} ({existing['link_id']}). Reason: {payload.reason.strip()}"
    )
    return {"status": "unlinked", "link_id": existing["link_id"], "source_case_id": src_id, "target_case_id": tgt_id}

@router.post("/{pid}/coordinate")
def execute_joint_coordination_directive(
    pid: str,
    payload: JointCoordinationRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    inst = db.fetch_one("SELECT * FROM institutions WHERE pid = ?", (pid.strip(),))
    if not inst:
        raise HTTPException(status_code=404, detail=f"Institution PID '{pid}' not found")

    active_cases = db.fetch_all("SELECT * FROM complaint_cases WHERE pid = ? AND status != 'Disposed'", (pid.strip(),))
    if not active_cases:
        raise HTTPException(status_code=400, detail="No active cases found for this institution to coordinate")

    curr_date = today_str(0)
    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)
    hold_val = 1 if payload.enforce_action_hold else 0
    note = f"Coordinated under Lead: {payload.lead_bureau} ({payload.resolution_protocol.strip()})"
    if hold_val:
        note += " [Action Hold Active on Non-Lead Bureaus]"

    db.execute(
        """UPDATE institutions
           SET coordination_status = 'COORDINATED',
               coordination_note = ?,
               lead_bureau = ?,
               action_hold = ?
           WHERE pid = ?""",
        (note, payload.lead_bureau, hold_val, pid.strip())
    )

    # Ensure pairwise links exist between all active cases of this institution
    for i in range(len(active_cases)):
        for j in range(i + 1, len(active_cases)):
            ca, cb = active_cases[i], active_cases[j]
            ex = db.fetch_one(
                """SELECT link_id FROM case_links
                   WHERE is_active = 1 AND (
                     (source_case_id = ? AND target_case_id = ?) OR
                     (source_case_id = ? AND target_case_id = ?)
                   )""",
                (ca["case_id"], cb["case_id"], cb["case_id"], ca["case_id"])
            )
            if not ex:
                lnk_id = f"LNK-{uuid.uuid4().hex[:8].upper()}"
                db.execute(
                    """INSERT INTO case_links (
                        link_id, source_case_id, target_case_id, pid, link_type, similarity_score,
                        lead_bureau, coordination_directive, linked_by, linked_at, is_active
                    ) VALUES (?, ?, ?, ?, 'JOINT_DIRECTIVE', 98.0, ?, ?, ?, ?, 1)""",
                    (
                        lnk_id,
                        ca["case_id"],
                        cb["case_id"],
                        pid.strip(),
                        payload.lead_bureau,
                        payload.resolution_protocol.strip(),
                        current_user["email"],
                        curr_ts
                    )
                )

    all_ids = [c["case_id"] for c in active_cases]
    for c in active_cases:
        others = [cid for cid in all_ids if cid != c["case_id"]]
        is_lead_case = (c["bureau"] == payload.lead_bureau)
        case_hold = 0 if is_lead_case else hold_val

        db.execute(
            """UPDATE complaint_cases
               SET last_action = ?, next_action = ?, last_action_date = ?, action_hold = ?
               WHERE case_id = ?""",
            (
                f"{curr_date}: [JOINT COORDINATION - LEAD: {payload.lead_bureau}] {payload.resolution_protocol.strip()}",
                f"Proceed under Lead Bureau ({payload.lead_bureau}) joint directive.",
                curr_date,
                case_hold,
                c["case_id"]
            )
        )
        db.execute(
            """INSERT INTO workflow_history (
                case_id, action_timestamp, action_type, from_bureau, to_bureau,
                officer, action_remarks, supporting_doc
            ) VALUES (?, ?, 'JOINT_COORDINATION', ?, ?, ?, ?, ?)""",
            (
                c["case_id"],
                curr_ts,
                c["bureau"],
                payload.lead_bureau if payload.lead_bureau != "JOINT_COMMITTEE" else c["bureau"],
                f"{current_user['full_name']} (Lead: {payload.lead_bureau})",
                f"{payload.resolution_protocol.strip()}. {payload.directive_notes.strip()} (Linked: {', '.join(others)})",
                f"Joint_Directive_{pid.strip()}.pdf"
            )
        )

    db.execute(
        "UPDATE alerts SET acknowledged = 1, acknowledged_by = ?, acknowledged_at = ? WHERE pid = ? AND alert_type = 'OVERLAP'",
        (current_user["email"], curr_ts, pid.strip())
    )

    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="JOINT_COORDINATION_DIRECTIVE",
        target_entity=pid.strip(),
        details=f"Executed Joint Coordination Directive for {inst['name']} ({pid}). Lead={payload.lead_bureau}, ActionHold={hold_val}"
    )
    return {
        "status": "coordinated",
        "pid": pid.strip(),
        "lead_bureau": payload.lead_bureau,
        "action_hold_enforced": bool(hold_val),
        "linked_cases": all_ids,
        "note": note
    }

@router.post("/{pid}/lead-bureau")
def assign_lead_bureau_and_hold(
    pid: str,
    payload: LeadBureauAssignmentRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Explicitly designates or updates the Lead Bureau and toggles the action hold on conflicting actions."""
    inst = db.fetch_one("SELECT * FROM institutions WHERE pid = ?", (pid.strip(),))
    if not inst:
        raise HTTPException(status_code=404, detail=f"Institution PID '{pid}' not found")

    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)
    hold_val = 1 if payload.action_hold else 0

    db.execute(
        "UPDATE institutions SET lead_bureau = ?, action_hold = ?, coordination_note = ? WHERE pid = ?",
        (payload.lead_bureau, hold_val, payload.coordination_directive, pid.strip())
    )

    # Update active cases action hold flag
    active_cases = db.fetch_all("SELECT * FROM complaint_cases WHERE pid = ? AND status != 'Disposed'", (pid.strip(),))
    for c in active_cases:
        is_lead = (c["bureau"] == payload.lead_bureau)
        db.execute(
            "UPDATE complaint_cases SET action_hold = ? WHERE case_id = ?",
            (0 if is_lead else hold_val, c["case_id"])
        )

    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="LEAD_BUREAU_ASSIGNED",
        target_entity=pid.strip(),
        details=f"Assigned Lead Bureau '{payload.lead_bureau}' for {inst['name']} ({pid}). ActionHold={payload.action_hold}"
    )

    return {
        "status": "updated",
        "pid": pid.strip(),
        "lead_bureau": payload.lead_bureau,
        "action_hold": payload.action_hold,
        "updated_at": curr_ts
    }

@router.post("/{pid}/release-hold")
def release_coordination_hold(
    pid: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Releases the action hold on non-lead bureaus after joint proceedings conclude."""
    inst = db.fetch_one("SELECT * FROM institutions WHERE pid = ?", (pid.strip(),))
    if not inst:
        raise HTTPException(status_code=404, detail=f"Institution PID '{pid}' not found")

    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)
    db.execute("UPDATE institutions SET action_hold = 0 WHERE pid = ?", (pid.strip(),))
    db.execute("UPDATE complaint_cases SET action_hold = 0 WHERE pid = ?", (pid.strip(),))

    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="ACTION_HOLD_RELEASED",
        target_entity=pid.strip(),
        details=f"Released action hold for institution {inst['name']} ({pid})."
    )

    return {
        "status": "released",
        "pid": pid.strip(),
        "action_hold": False,
        "released_by": current_user["email"],
        "released_at": curr_ts
    }
