"""
Live SLA Computation & Dynamic Alert Generation Service
=======================================================
Computes SLA countdowns, overdue breaches, approaching deadlines,
and cross-bureau overlap alerts dynamically from the relational database.
Uses settings.SLA_DEFAULT_DAYS and category-specific `sla_configs` rows.
"""
from datetime import datetime
from typing import Any, Dict, List
from app.core.config import settings
from app.core.database import db, now_ist, today_str, timestamp_str

def _days_between(d1_str: str, d2_str: str) -> int:
    try:
        d1 = datetime.strptime(d1_str[:10], "%Y-%m-%d").date()
        d2 = datetime.strptime(d2_str[:10], "%Y-%m-%d").date()
        return (d2 - d1).days
    except Exception:
        return 0

def get_sla_config_map() -> Dict[str, Dict[str, Any]]:
    rows = db.fetch_all("SELECT * FROM sla_configs")
    return {r["category"]: r for r in rows}

def compute_expected_disposal_date(receipt_date_str: str, category: str, override_days: int = 0) -> str:
    sla_map = get_sla_config_map()
    if override_days and override_days > 0:
        days = override_days
    elif category in sla_map:
        days = int(sla_map[category]["prescribed_days"])
    else:
        days = settings.SLA_DEFAULT_DAYS
    try:
        from datetime import timedelta
        dt = datetime.strptime(receipt_date_str[:10], "%Y-%m-%d") + timedelta(days=days)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return today_str(days)

def evaluate_case_sla(case_row: Dict[str, Any], sla_map: Dict[str, Dict[str, Any]] = None) -> Dict[str, Any]:
    if sla_map is None:
        sla_map = get_sla_config_map()
    curr_date = today_str(0)
    age_days = max(0, _days_between(case_row.get("receipt_date", curr_date), curr_date))
    cat = case_row.get("category", "")
    cat_cfg = sla_map.get(cat, {
        "prescribed_days": settings.SLA_DEFAULT_DAYS,
        "warning_threshold_days": settings.SLA_WARNING_DAYS,
        "pending_reminder_days": settings.PENDING_REMINDER_DAYS,
        "escalation_authority": "Competent Authority (AICTE)"
    })
    warn_days = int(cat_cfg.get("warning_threshold_days", settings.SLA_WARNING_DAYS))
    rem_days_cfg = int(cat_cfg.get("pending_reminder_days", settings.PENDING_REMINDER_DAYS))

    if case_row.get("status") == "Disposed":
        return {
            "state": "DISPOSED",
            "label": "Disposed Within SLA",
            "days_remaining": 0,
            "age_days": age_days,
            "prescribed_days": cat_cfg["prescribed_days"],
            "escalation_authority": cat_cfg["escalation_authority"],
            "pending_reminder_due": False
        }

    exp_date = case_row.get("expected_disposal_date", curr_date)
    rem = _days_between(curr_date, exp_date)
    pending_reminder_due = (case_row.get("status") == "Pending" and age_days >= rem_days_cfg)

    if rem < 0:
        return {
            "state": "BREACHED",
            "label": f"SLA Breached ({abs(rem)}d overdue)",
            "days_remaining": rem,
            "age_days": age_days,
            "prescribed_days": cat_cfg["prescribed_days"],
            "escalation_authority": cat_cfg["escalation_authority"],
            "pending_reminder_due": pending_reminder_due
        }
    if rem <= warn_days:
        return {
            "state": "APPROACHING",
            "label": f"Approaching SLA ({rem}d left)",
            "days_remaining": rem,
            "age_days": age_days,
            "prescribed_days": cat_cfg["prescribed_days"],
            "escalation_authority": cat_cfg["escalation_authority"],
            "pending_reminder_due": pending_reminder_due
        }
    return {
        "state": "ON_TRACK",
        "label": f"{rem}d remaining",
        "days_remaining": rem,
        "age_days": age_days,
        "prescribed_days": cat_cfg["prescribed_days"],
        "escalation_authority": cat_cfg["escalation_authority"],
        "pending_reminder_due": pending_reminder_due
    }

def sync_and_fetch_live_alerts() -> List[Dict[str, Any]]:
    """
    Dynamically computes alerts from live institution overlaps and case SLA states,
    upserts them into the `alerts` table (preserving acknowledged status), and returns the sorted feed.
    """
    sla_map = get_sla_config_map()
    institutions = {i["pid"]: i for i in db.fetch_all("SELECT * FROM institutions")}
    cases = db.fetch_all("SELECT * FROM complaint_cases")
    existing_alerts = {a["alert_id"]: a for a in db.fetch_all("SELECT * FROM alerts")}

    now_ts = timestamp_str(0, now_ist().hour, now_ist().minute)
    active_alert_ids = set()

    # 1. Cross-Bureau Overlap Alerts per Institution
    by_pid: Dict[str, List[Dict[str, Any]]] = {}
    for c in cases:
        if c["status"] != "Disposed":
            by_pid.setdefault(c["pid"], []).append(c)

    for pid, active_list in by_pid.items():
        bureaus = sorted(list({c["bureau"] for c in active_list}))
        inst = institutions.get(pid, {})
        if len(bureaus) > 1 and inst.get("coordination_status") != "COORDINATED":
            aid = f"ALT-OVERLAP-{pid}"
            active_alert_ids.add(aid)
            case_summaries = ", ".join(f"{c['bureau']} ({c['case_id']})" for c in active_list)
            title = f"Potential Overlapping Cases – Coordination Required ({' + '.join(bureaus)})"
            msg = (
                f"{inst.get('name', pid)} (PID: {pid}) has {len(active_list)} concurrent active cases across "
                f"{', '.join(bureaus)}: {case_summaries}. Immediate coordination required to avoid duplicate action."
            )
            if aid not in existing_alerts:
                db.execute(
                    """INSERT INTO alerts (alert_id, alert_type, severity, pid, case_id, title, message, created_at, acknowledged)
                       VALUES (?, 'OVERLAP', 'CRITICAL', ?, ?, ?, ?, ?, 0)""",
                    (aid, pid, active_list[0]["case_id"], title, msg, now_ts)
                )
            else:
                db.execute(
                    "UPDATE alerts SET title = ?, message = ? WHERE alert_id = ?",
                    (title, msg, aid)
                )

    # 2. Case-Level SLA Breach, Approaching Deadline, Pending Reminder, and Escalation Alerts
    for c in cases:
        if c["status"] == "Disposed":
            continue
        sla = evaluate_case_sla(c, sla_map)
        inst_name = institutions.get(c["pid"], {}).get("name", c["inst_variant_logged"])

        if sla["state"] == "BREACHED":
            aid = f"ALT-BREACH-{c['case_id']}"
            active_alert_ids.add(aid)
            overdue_d = abs(sla["days_remaining"])
            title = f"SLA Timeline Breached ({overdue_d} Days Overdue) — {c['category']}"
            msg = (
                f"Case {c['case_id']} ({inst_name}, {c['bureau']}) exceeded its prescribed {sla['prescribed_days']}-day "
                f"disposal deadline ({c['expected_disposal_date']}). Escalation authority: {sla['escalation_authority']}."
            )
            if aid not in existing_alerts:
                db.execute(
                    """INSERT INTO alerts (alert_id, alert_type, severity, pid, case_id, title, message, created_at, acknowledged)
                       VALUES (?, 'SLA_BREACH', 'CRITICAL', ?, ?, ?, ?, ?, 0)""",
                    (aid, c["pid"], c["case_id"], title, msg, now_ts)
                )
        elif sla["state"] == "APPROACHING":
            aid = f"ALT-APPROACH-{c['case_id']}"
            active_alert_ids.add(aid)
            rem_d = sla["days_remaining"]
            title = f"Approaching Prescribed SLA Deadline ({rem_d} Days Remaining) — {c['case_id']}"
            msg = (
                f"Case {c['case_id']} ({inst_name}, Assigned: {c['assigned_officer']}) is due for disposal on "
                f"{c['expected_disposal_date']}."
            )
            if aid not in existing_alerts:
                db.execute(
                    """INSERT INTO alerts (alert_id, alert_type, severity, pid, case_id, title, message, created_at, acknowledged)
                       VALUES (?, 'APPROACHING_SLA', 'HIGH', ?, ?, ?, ?, ?, 0)""",
                    (aid, c["pid"], c["case_id"], title, msg, now_ts)
                )

        if sla["pending_reminder_due"]:
            aid = f"ALT-PENDING-{c['case_id']}"
            active_alert_ids.add(aid)
            title = f"Pending Case Scrutiny Reminder ({sla['age_days']} Days in Pending) — {c['case_id']}"
            msg = (
                f"Case {c['case_id']} ({inst_name}, {c['bureau']}) has remained in 'Pending' status for "
                f"{sla['age_days']} days since receipt ({c['receipt_date']}). Initiate inquiry or assign committee."
            )
            if aid not in existing_alerts:
                db.execute(
                    """INSERT INTO alerts (alert_id, alert_type, severity, pid, case_id, title, message, created_at, acknowledged)
                       VALUES (?, 'PENDING_REMINDER', 'MEDIUM', ?, ?, ?, ?, ?, 0)""",
                    (aid, c["pid"], c["case_id"], title, msg, now_ts)
                )

        if c["status"] == "Escalated":
            aid = f"ALT-ESC-{c['case_id']}"
            active_alert_ids.add(aid)
            title = f"Escalated Case Awaiting Competent Authority Directive — {c['case_id']}"
            msg = f"Case {c['case_id']} ({inst_name}) is currently escalated to {sla['escalation_authority']}."
            if aid not in existing_alerts:
                db.execute(
                    """INSERT INTO alerts (alert_id, alert_type, severity, pid, case_id, title, message, created_at, acknowledged)
                       VALUES (?, 'ESCALATION', 'HIGH', ?, ?, ?, ?, ?, 0)""",
                    (aid, c["pid"], c["case_id"], title, msg, now_ts)
                )

    return db.fetch_all("SELECT * FROM alerts ORDER BY acknowledged ASC, severity ASC, created_at DESC")
