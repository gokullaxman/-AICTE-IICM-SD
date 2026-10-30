import csv
import io
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from typing import Any, Dict, List, Optional
from app.core.database import db, now_ist
from app.core.security import get_current_user
from app.services.sla_and_alerts import evaluate_case_sla, get_sla_config_map

router = APIRouter()

def _load_enriched_dataset():
    institutions = {i["pid"]: i for i in db.fetch_all("SELECT * FROM institutions")}
    cases = db.fetch_all("SELECT * FROM complaint_cases")
    sla_map = get_sla_config_map()
    for c in cases:
        c["sla"] = evaluate_case_sla(c, sla_map)
        c["institution"] = institutions.get(c["pid"], {})
    return institutions, cases

@router.get("/summary")
def get_executive_summary():
    institutions, cases = _load_enriched_dataset()
    total = len(cases)
    disposed = sum(1 for c in cases if c["status"] == "Disposed")
    pending = sum(1 for c in cases if c["status"] == "Pending")
    in_proc = sum(1 for c in cases if c["status"] == "Under Process")
    transferred = sum(1 for c in cases if c["status"] == "Transferred")
    escalated = sum(1 for c in cases if c["status"] == "Escalated")
    breached = sum(1 for c in cases if c["sla"]["state"] == "BREACHED")
    approaching = sum(1 for c in cases if c["sla"]["state"] == "APPROACHING")

    by_pid: Dict[str, set] = {}
    for c in cases:
        if c["status"] != "Disposed":
            by_pid.setdefault(c["pid"], set()).add(c["bureau"])
    overlap_inst_count = sum(1 for pid, bset in by_pid.items() if len(bset) > 1)

    return {
        "generated_at": now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
        "total_institutions": len(institutions),
        "total_cases": total,
        "pending_cases": pending,
        "under_process_cases": in_proc,
        "transferred_cases": transferred,
        "escalated_cases": escalated,
        "disposed_cases": disposed,
        "sla_breached_cases": breached,
        "sla_approaching_cases": approaching,
        "overlapping_institutions_count": overlap_inst_count,
        "disposal_rate_percent": round((disposed / total) * 100.0, 1) if total else 0.0
    }

@router.get("/bureau-wise")
def get_bureau_wise_report():
    _, cases = _load_enriched_dataset()
    bureaus = db.fetch_all("SELECT * FROM bureaus WHERE code != 'HQ'")
    items = []
    for b in bureaus:
        code = b["code"]
        bc = [c for c in cases if c["bureau"] == code]
        total = len(bc)
        disp = sum(1 for c in bc if c["status"] == "Disposed")
        breached = sum(1 for c in bc if c["sla"]["state"] == "BREACHED")
        avg_age = round(sum(c["sla"]["age_days"] for c in bc) / total, 1) if total else 0.0
        items.append({
            "bureau_code": code,
            "bureau_name": b["name"],
            "total": total,
            "pending": sum(1 for c in bc if c["status"] == "Pending"),
            "under_process": sum(1 for c in bc if c["status"] == "Under Process"),
            "transferred": sum(1 for c in bc if c["status"] == "Transferred"),
            "escalated": sum(1 for c in bc if c["status"] == "Escalated"),
            "disposed": disp,
            "sla_breached": breached,
            "avg_age_days": avg_age,
            "disposal_rate_percent": round((disp / total) * 100.0, 1) if total else 0.0
        })
    return {"items": items}

@router.get("/region-wise")
def get_region_wise_report():
    institutions, cases = _load_enriched_dataset()
    regions = sorted(list({i["region"] for i in institutions.values()}))
    items = []
    for r in regions:
        r_pids = {pid for pid, inst in institutions.items() if inst["region"] == r}
        rc = [c for c in cases if c["pid"] in r_pids]
        disp = sum(1 for c in rc if c["status"] == "Disposed")
        breached = sum(1 for c in rc if c["sla"]["state"] == "BREACHED")
        active_by_pid: Dict[str, set] = {}
        for c in rc:
            if c["status"] != "Disposed":
                active_by_pid.setdefault(c["pid"], set()).add(c["bureau"])
        overlaps = sum(1 for bset in active_by_pid.values() if len(bset) > 1)
        items.append({
            "region": r,
            "institutions_count": len(r_pids),
            "total_cases": len(rc),
            "active_overlaps": overlaps,
            "sla_breached": breached,
            "disposed": disp,
            "disposal_rate_percent": round((disp / len(rc)) * 100.0, 1) if rc else 0.0
        })
    return {"items": items}

@router.get("/institution-wise")
def get_institution_wise_report():
    institutions, cases = _load_enriched_dataset()
    items = []
    for pid, inst in institutions.items():
        ic = [c for c in cases if c["pid"] == pid]
        active = [c for c in ic if c["status"] != "Disposed"]
        bureaus = sorted(list({c["bureau"] for c in active}))
        items.append({
            "pid": pid,
            "name": inst["name"],
            "city": inst["city"],
            "state": inst["state"],
            "region": inst["region"],
            "total_cases": len(ic),
            "active_cases": len(active),
            "disposed_cases": len(ic) - len(active),
            "active_bureaus": bureaus,
            "has_overlap": len(bureaus) > 1,
            "coordination_status": inst["coordination_status"],
            "sla_breached_count": sum(1 for c in active if c["sla"]["state"] == "BREACHED")
        })
    items.sort(key=lambda x: (-x["active_cases"], -x["total_cases"]))
    return {"items": items}

@router.get("/ageing")
def get_pending_ageing_analysis():
    _, cases = _load_enriched_dataset()
    active = [c for c in cases if c["status"] != "Disposed"]
    bureaus = ["RB", "PGRC", "AB", "VC", "RO"]
    buckets = {
        "0_to_15_days": {b: 0 for b in bureaus},
        "16_to_30_days": {b: 0 for b in bureaus},
        "31_to_45_days": {b: 0 for b in bureaus},
        "over_45_days": {b: 0 for b in bureaus}
    }
    for c in active:
        b = c["bureau"]
        age = c["sla"]["age_days"]
        if b not in bureaus:
            continue
        if age <= 15:
            buckets["0_to_15_days"][b] += 1
        elif age <= 30:
            buckets["16_to_30_days"][b] += 1
        elif age <= 45:
            buckets["31_to_45_days"][b] += 1
        else:
            buckets["over_45_days"][b] += 1
    return {"bureaus": bureaus, "buckets": buckets, "total_active": len(active)}

@router.get("/disposal-rate")
def get_disposal_rate_analytics():
    _, cases = _load_enriched_dataset()
    categories = sorted(list({c["category"] for c in cases}))
    by_category = []
    for cat in categories:
        cc = [c for c in cases if c["category"] == cat]
        disp = sum(1 for c in cc if c["status"] == "Disposed")
        by_category.append({
            "category": cat,
            "total": len(cc),
            "disposed": disp,
            "active": len(cc) - disp,
            "disposal_rate_percent": round((disp / len(cc)) * 100.0, 1) if cc else 0.0
        })
    return {"by_category": by_category}

@router.get("/monthly-quarterly")
def get_monthly_quarterly_performance():
    """Computes intake, disposal, and linked cross-bureau overlaps directly from database complaint_cases."""
    _, cases = _load_enriched_dataset()
    links = db.fetch_all("SELECT * FROM case_links WHERE is_active = 1")
    linked_case_ids = {l["source_case_id"] for l in links} | {l["target_case_id"] for l in links}

    now = now_ist().date()
    buckets = []
    for i in range(4, -1, -1):
        end_d = now - timedelta(days=i * 15)
        start_d = end_d - timedelta(days=14)
        label = f"{start_d.strftime('%d %b')}–{end_d.strftime('%d %b')}"
        start_s, end_s = start_d.isoformat(), end_d.isoformat()
        in_window = [
            c for c in cases
            if start_s <= c["receipt_date"][:10] <= end_s
        ]
        buckets.append({
            "period": label,
            "received": len(in_window),
            "disposed": sum(1 for c in in_window if c["status"] == "Disposed"),
            "overlapping_linked": sum(1 for c in in_window if c["case_id"] in linked_case_ids)
        })
    return {"cohort_series": buckets, "total_cases_evaluated": len(cases)}

# ============================================================================
# Feature 7: Tamper-Evident Hash-Chained Audit Trail Verification
# ============================================================================

@router.get("/audit-logs")
def list_audit_logs(limit: int = 100):
    """Returns the immutable audit log ledger containing cryptographic SHA-256 hash chains."""
    rows = db.fetch_all("SELECT * FROM audit_logs ORDER BY audit_id DESC LIMIT ?", (limit,))
    return {
        "count": len(rows),
        "items": rows
    }

@router.get("/audit-verify")
@router.post("/audit-verify")
def verify_audit_log_cryptographic_integrity():
    """
    Traverses the entire audit log chain from Genesis to Head, recalculates each SHA-256 hash,
    and mathematically verifies that no record has been tampered with, deleted, or injected.
    """
    verification = db.verify_audit_chain()
    return {
        "verified": verification["valid"],
        "total_records_checked": verification.get("count", 0),
        "chain_head_hash": verification.get("chain_head", "0" * 64),
        "status": verification.get("status", "TAMPER_DETECTED" if not verification["valid"] else "VERIFIED"),
        "error_details": verification.get("error"),
        "tampered_audit_id": verification.get("tampered_audit_id"),
        "cryptographic_standard": "SHA-256 Blockchain-Style Hash Chaining"
    }

@router.get("/export/csv")
def export_server_csv_report():
    institutions, cases = _load_enriched_dataset()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Case ID", "AICTE Permanent ID", "Institution Name", "Docket Name Variant",
        "City", "State", "Region", "Receipt Date", "Category", "Bureau",
        "Assigned Officer", "Status", "Expected Disposal Date", "SLA State",
        "Days Remaining", "Age Days", "Prescribed SLA Days", "Match Method", "Match Confidence %"
    ])
    for c in cases:
        inst = institutions.get(c["pid"], {})
        writer.writerow([
            c["case_id"],
            c["pid"],
            inst.get("name", ""),
            c["inst_variant_logged"],
            inst.get("city", ""),
            inst.get("state", ""),
            inst.get("region", ""),
            c["receipt_date"],
            c["category"],
            c["bureau"],
            c["assigned_officer"],
            c["status"],
            c["expected_disposal_date"],
            c["sla"]["state"],
            c["sla"]["days_remaining"],
            c["sla"]["age_days"],
            c["sla"]["prescribed_days"],
            c["match_method"],
            c["match_confidence"]
        ])
    output.seek(0)
    filename = f"AICTE_IICM_MIS_Report_{now_ist().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@router.get("/export/pdf")
def export_server_pdf_report():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24
    )
    story = []
    styles = getSampleStyleSheet()

    institutions, cases = _load_enriched_dataset()
    summary = get_executive_summary()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#0f2547"),
    )
    sub_style = ParagraphStyle(
        "DocSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#475569"),
    )

    story.append(Paragraph("ALL INDIA COUNCIL FOR TECHNICAL EDUCATION (AICTE)", title_style))
    story.append(Paragraph(
        f"Integrated Institution Complaint Monitoring & Status Dashboard (IICM-SD) — Executive MIS Brief<br/>"
        f"Generated: {now_ist().strftime('%d %b %Y, %H:%M:%S IST')} | Dual DB Relational Build (v3.1.0)",
        sub_style
    ))
    story.append(Spacer(1, 12))

    kpi_data = [
        ["Total Institutions", "Total Cases", "Pending", "Under Process", "Transferred/Esc", "Disposed", "SLA Breached", "Overlapping Insts"],
        [
            str(summary["total_institutions"]),
            str(summary["total_cases"]),
            str(summary["pending_cases"]),
            str(summary["under_process_cases"]),
            str(summary["transferred_cases"] + summary["escalated_cases"]),
            f"{summary['disposed_cases']} ({summary['disposal_rate_percent']}%)",
            str(summary["sla_breached_cases"]),
            str(summary["overlapping_institutions_count"])
        ]
    ]
    kpi_table = Table(kpi_data, colWidths=[95] * 8)
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f2547")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f8fafc")),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 14))

    rows = [["Case ID", "AICTE PID", "Institution Name", "Region", "Bureau", "Category", "Status", "SLA Status"]]
    for c in cases:
        inst = institutions.get(c["pid"], {})
        rows.append([
            c["case_id"],
            c["pid"],
            (inst.get("name", "")[:34] + "...") if len(inst.get("name", "")) > 34 else inst.get("name", ""),
            inst.get("region", ""),
            c["bureau"],
            (c["category"][:26] + "...") if len(c["category"]) > 26 else c["category"],
            c["status"],
            c["sla"]["label"]
        ])

    case_table = Table(rows, colWidths=[85, 75, 175, 75, 50, 140, 75, 110])
    case_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ("PADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(case_table)
    doc.build(story)
    buffer.seek(0)

    filename = f"AICTE_IICM_Official_Audit_Brief_{now_ist().strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
