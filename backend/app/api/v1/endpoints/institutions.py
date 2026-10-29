import os
import tempfile
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from typing import Any, Dict, List, Optional
from app.core.database import db
from app.core.security import get_current_user
from app.services.sla_and_alerts import evaluate_case_sla, get_sla_config_map
from app.services.overlap_engine import CrossBureauOverlapEngine

router = APIRouter()
engine = CrossBureauOverlapEngine()

def _format_inst(row: Dict[str, Any]) -> Dict[str, Any]:
    aliases_raw = row.get("aliases", "")
    aliases = [a.strip() for a in aliases_raw.split("|") if a.strip()] if isinstance(aliases_raw, str) else list(aliases_raw or [])
    return {
        **row,
        "aliases": aliases,
        "intake": row.get("sanctioned_intake", 0),
        "established": row.get("established_year", 2005),
        "principal": row.get("principal_name", ""),
        "approvalStatus": row.get("approval_status", ""),
        "coordinationStatus": row.get("coordination_status", "SINGLE"),
        "coordinationNote": row.get("coordination_note", "")
    }

@router.get("/")
def list_institutions(
    q: Optional[str] = None,
    region: Optional[str] = None,
    state: Optional[str] = None,
    coordination_status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50
):
    rows = [_format_inst(r) for r in db.fetch_all("SELECT * FROM institutions ORDER BY name ASC")]
    if q and q.strip():
        ql = q.strip().lower()
        rows = [
            i for i in rows
            if ql in i["name"].lower()
            or ql in i["pid"].lower()
            or ql in i["city"].lower()
            or ql in i["state"].lower()
            or any(ql in al.lower() for al in i["aliases"])
        ]
    if region and region != "ALL":
        rows = [i for i in rows if i["region"].lower() == region.lower()]
    if state and state != "ALL":
        rows = [i for i in rows if i["state"].lower() == state.lower()]
    if coordination_status and coordination_status != "ALL":
        rows = [i for i in rows if i["coordinationStatus"].upper() == coordination_status.upper()]

    total = len(rows)
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    start = (page - 1) * page_size
    paged = rows[start : start + page_size]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "items": paged
    }

@router.get("/{pid}")
@router.get("/{pid}/profile")
def get_institution_consolidated_profile(pid: str):
    raw_inst = db.fetch_one("SELECT * FROM institutions WHERE pid = ?", (pid.strip(),))
    if not raw_inst:
        raise HTTPException(status_code=404, detail=f"Institution with AICTE Permanent ID '{pid}' not found")
    inst = _format_inst(raw_inst)
    sla_map = get_sla_config_map()

    cases_raw = db.fetch_all("SELECT * FROM complaint_cases WHERE pid = ? ORDER BY receipt_date DESC", (inst["pid"],))
    links_raw = db.fetch_all("SELECT * FROM case_links WHERE pid = ? AND is_active = 1", (inst["pid"],))

    enriched_cases = []
    for c in cases_raw:
        history = db.fetch_all(
            "SELECT * FROM workflow_history WHERE case_id = ? ORDER BY history_id ASC",
            (c["case_id"],)
        )
        docs = db.fetch_all(
            "SELECT * FROM uploaded_documents WHERE case_id = ? ORDER BY uploaded_at DESC",
            (c["case_id"],)
        )
        linked_ids = [
            (lnk["target_case_id"] if lnk["source_case_id"] == c["case_id"] else lnk["source_case_id"])
            for lnk in links_raw
            if lnk["source_case_id"] == c["case_id"] or lnk["target_case_id"] == c["case_id"]
        ]
        sla = evaluate_case_sla(c, sla_map)
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
                for h in history
            ],
            "documents": docs
        })

    active_cases = [c for c in enriched_cases if c["status"] != "Disposed"]
    active_bureaus = sorted(list({c["bureau"] for c in active_cases}))
    bureau_footprint = {}
    for b_code in ["RB", "PGRC", "AB", "VC", "RO"]:
        b_all = [c for c in enriched_cases if c["bureau"] == b_code]
        b_act = [c for c in b_all if c["status"] != "Disposed"]
        bureau_footprint[b_code] = {
            "total": len(b_all),
            "active": len(b_act),
            "disposed": len(b_all) - len(b_act)
        }

    return {
        "institution": inst,
        "bureau_footprint": bureau_footprint,
        "active_bureaus": active_bureaus,
        "has_overlap": len(active_bureaus) > 1,
        "overlap_banner": (
            "Potential Overlapping Cases – Coordination Required"
            if len(active_bureaus) > 1 and inst["coordinationStatus"] != "COORDINATED"
            else ("Coordinated – Joint Action Active" if len(active_bureaus) > 1 else "Single Bureau / Clean")
        ),
        "active_case_links": links_raw,
        "consolidated_case_history": enriched_cases
    }

@router.post("/import-csv")
async def import_approved_institutions_csv(
    file: Optional[UploadFile] = File(default=None),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    if file is not None:
        content = await file.read()
        with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            count = db.import_institutions_from_csv(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    else:
        default_csv = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../data/aicte_approved_institutions_public.csv"))
        count = db.import_institutions_from_csv(default_csv)

    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="INSTITUTION_CSV_IMPORT",
        target_entity="institutions",
        details=f"Imported {count} new AICTE-approved institution record(s)."
    )
    return {"status": "imported", "newly_imported_count": count}
