"""
Live SLA Computation & Dynamic Alert Generation Service
=======================================================
Computes SLA countdowns, overdue breaches, approaching deadlines,
and cross-bureau overlap alerts dynamically from the relational database.
Implements the Multi-Tier Auto-Escalation Ladder on SLA breach.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from app.core.config import settings
from app.core.database import db, now_ist, today_str, timestamp_str
from app.services.notification_service import broadcast_alert_notifications

ESCALATION_LADDER: List[Dict[str, Any]] = [
    {
        "level": 0,
        "name": "Desk Officer / Scrutiny Section",
        "title": "Level 0: Desk Officer Scrutiny",
        "min_overdue_days": 0
    },
    {
        "level": 1,
        "name": "Assistant Director / Regional Officer",
        "title": "Level 1: Assistant Director / Regional Officer Intervention",
        "min_overdue_days": 1
    },
    {
        "level": 2,
        "name": "Bureau Head / Advisor / Director",
        "title": "Level 2: Bureau Head & Advisor Direct Summons",
        "min_overdue_days": 8
    },
    {
        "level": 3,
        "name": "Competent Authority / Member Secretary / Chairman Desk",
        "title": "Level 3: Apex Competent Authority / Standing Hearing Committee",
        "min_overdue_days": 15
    }
]

def get_escalation_tier(overdue_days: int) -> Dict[str, Any]:
    if overdue_days >= 15:
        return ESCALATION_LADDER[3]
    elif overdue_days >= 8:
        return ESCALATION_LADDER[2]
    elif overdue_days >= 1:
        return ESCALATION_LADDER[1]
    return ESCALATION_LADDER[0]

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

    esc_level = int(case_row.get("escalation_level") or 0)
    tier_info = next((t for t in ESCALATION_LADDER if t["level"] == esc_level), ESCALATION_LADDER[0])

    if case_row.get("status") == "Disposed":
        return {
            "state": "DISPOSED",
            "label": "Disposed Within SLA",
            "days_remaining": 0,
            "age_days": age_days,
            "prescribed_days": cat_cfg["prescribed_days"],
            "escalation_authority": cat_cfg["escalation_authority"],
            "escalation_level": esc_level,
            "escalation_tier": tier_info["name"],
            "pending_reminder_due": False
        }

    exp_date = case_row.get("expected_disposal_date", curr_date)
    rem = _days_between(curr_date, exp_date)
    pending_reminder_due = (case_row.get("status") == "Pending" and age_days >= rem_days_cfg)

    if rem < 0:
        overdue_d = abs(rem)
        ladder_tier = get_escalation_tier(overdue_d)
        return {
            "state": "BREACHED",
            "label": f"SLA Breached ({overdue_d}d overdue)",
            "days_remaining": rem,
            "age_days": age_days,
            "prescribed_days": cat_cfg["prescribed_days"],
            "escalation_authority": cat_cfg["escalation_authority"],
            "escalation_level": esc_level,
            "escalation_tier": tier_info["name"],
            "ladder_target_level": ladder_tier["level"],
            "ladder_target_name": ladder_tier["name"],
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
            "escalation_level": esc_level,
            "escalation_tier": tier_info["name"],
            "pending_reminder_due": pending_reminder_due
        }
    return {
        "state": "ON_TRACK",
        "label": f"{rem}d remaining",
        "days_remaining": rem,
        "age_days": age_days,
        "prescribed_days": cat_cfg["prescribed_days"],
        "escalation_authority": cat_cfg["escalation_authority"],
        "escalation_level": esc_level,
        "escalation_tier": tier_info["name"],
        "pending_reminder_due": pending_reminder_due
    }

def run_auto_escalation_ladder() -> List[Dict[str, Any]]:
    """
    Evaluates active cases against the Auto-Escalation Ladder.
    If a case is overdue past its SLA and its escalation level is lower than the required tier,
    it automatically promotes the case status to 'Escalated', updates escalation_level,
    records workflow history, triggers critical alerts, and logs a tamper-evident audit record.
    """
    sla_map = get_sla_config_map()
    cases = db.fetch_all("SELECT * FROM complaint_cases WHERE status != 'Disposed'")
    curr_date = today_str(0)
    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)
    escalated_items = []

    for c in cases:
        sla = evaluate_case_sla(c, sla_map)
        if sla["state"] != "BREACHED":
            continue

        overdue_days = abs(sla["days_remaining"])
        target_tier = get_escalation_tier(overdue_days)
        current_level = int(c.get("escalation_level") or 0)

        if current_level < target_tier["level"]:
            new_level = target_tier["level"]
            new_status_str = f"ESCALATED_L{new_level}"
            authority = target_tier["name"]

            # Update complaint case
            db.execute(
                """UPDATE complaint_cases
                   SET status = 'Escalated',
                       escalation_level = ?,
                       escalation_status = ?,
                       last_escalated_at = ?,
                       last_action = ?,
                       next_action = ?,
                       last_action_date = ?
                   WHERE case_id = ?""",
                (
                    new_level,
                    new_status_str,
                    curr_ts,
                    f"{curr_date}: [AUTO-ESCALATION LADDER L{new_level}] Case overdue by {overdue_days} days. Promoted to {authority}.",
                    f"Immediate hearing and disposal directive from {authority} under statutory SLA enforcement.",
                    curr_date,
                    c["case_id"]
                )
            )

            # Insert workflow history
            db.execute(
                """INSERT INTO workflow_history (
                    case_id, action_timestamp, action_type, from_bureau, to_bureau,
                    officer, action_remarks, supporting_doc
                ) VALUES (?, ?, 'AUTO_ESCALATION', ?, ?, ?, ?, ?)""",
                (
                    c["case_id"],
                    curr_ts,
                    c["bureau"],
                    c["bureau"],
                    f"System SLA Ladder Daemon ({authority})",
                    f"Automatic Escalation Ladder Triggered: Case {c['case_id']} is {overdue_days} days overdue (SLA: {sla['prescribed_days']}d). Promoted from Level {current_level} to Level {new_level} ({authority}).",
                    f"Auto_Escalation_Order_L{new_level}_{c['case_id']}.pdf"
                )
            )

            # Log hash-chained audit event
            db.log_audit(
                actor_email="system.sla@aicte-india.org",
                actor_role="SYSTEM_AUTOMATION",
                actor_bureau=c["bureau"],
                event_type="AUTO_ESCALATION_LADDER_TRIGGERED",
                target_entity=c["case_id"],
                details=f"Case {c['case_id']} automatically escalated to Level {new_level} ({authority}) due to {overdue_days} days SLA breach."
            )

            escalated_items.append({
                "case_id": c["case_id"],
                "pid": c["pid"],
                "bureau": c["bureau"],
                "overdue_days": overdue_days,
                "previous_level": current_level,
                "new_level": new_level,
                "authority": authority
            })

    return escalated_items

def sync_and_fetch_live_alerts() -> List[Dict[str, Any]]:
    """
    Dynamically computes alerts from live institution overlaps and case SLA states,
    triggers the Auto-Escalation Ladder, upserts alerts, broadcasts SMS/Email notifications,
    and returns the feed.
    """
    # 1. Run Auto-Escalation Ladder for any overdue cases
    run_auto_escalation_ladder()

    sla_map = get_sla_config_map()
    institutions = {i["pid"]: i for i in db.fetch_all("SELECT * FROM institutions")}
    cases = db.fetch_all("SELECT * FROM complaint_cases")
    existing_alerts = {a["alert_id"]: a for a in db.fetch_all("SELECT * FROM alerts")}

    now_ts = timestamp_str(0, now_ist().hour, now_ist().minute)
    active_alert_ids = set()

    # 2. Cross-Bureau Overlap Alerts per Institution
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
                broadcast_alert_notifications({
                    "alert_id": aid,
                    "case_id": active_list[0]["case_id"],
                    "alert_type": "OVERLAP",
                    "title": title,
                    "message": msg,
                    "pid": pid
                })
            else:
                db.execute(
                    "UPDATE alerts SET title = ?, message = ? WHERE alert_id = ?",
                    (title, msg, aid)
                )

    # 3. Case-Level SLA Breach, Approaching Deadline, Pending Reminder, and Escalation Alerts
    for c in cases:
        if c["status"] == "Disposed":
            continue
        sla = evaluate_case_sla(c, sla_map)
        inst_name = institutions.get(c["pid"], {}).get("name", c["inst_variant_logged"])

        if sla["state"] == "BREACHED":
            aid = f"ALT-BREACH-{c['case_id']}"
            active_alert_ids.add(aid)
            overdue_d = abs(sla["days_remaining"])
            esc_tier_name = sla.get("ladder_target_name") or sla.get("escalation_authority")
            title = f"SLA Timeline Breached ({overdue_d} Days Overdue) — {c['category']}"
            msg = (
                f"Case {c['case_id']} ({inst_name}, {c['bureau']}) exceeded its prescribed {sla['prescribed_days']}-day "
                f"disposal deadline ({c['expected_disposal_date']}). Auto-Escalation Target: {esc_tier_name}."
            )
            if aid not in existing_alerts:
                db.execute(
                    """INSERT INTO alerts (alert_id, alert_type, severity, pid, case_id, title, message, created_at, acknowledged)
                       VALUES (?, 'SLA_BREACH', 'CRITICAL', ?, ?, ?, ?, ?, 0)""",
                    (aid, c["pid"], c["case_id"], title, msg, now_ts)
                )
                broadcast_alert_notifications({
                    "alert_id": aid,
                    "case_id": c["case_id"],
                    "alert_type": "SLA_BREACH",
                    "title": title,
                    "message": msg,
                    "pid": c["pid"]
                })
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

        if sla.get("pending_reminder_due"):
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
            title = f"Escalated Case Awaiting Directive (Level {c.get('escalation_level', 1)}) — {c['case_id']}"
            msg = f"Case {c['case_id']} ({inst_name}) is escalated to {sla.get('escalation_tier', 'Competent Authority')}."
            if aid not in existing_alerts:
                db.execute(
                    """INSERT INTO alerts (alert_id, alert_type, severity, pid, case_id, title, message, created_at, acknowledged)
                       VALUES (?, 'ESCALATION', 'HIGH', ?, ?, ?, ?, ?, 0)""",
                    (aid, c["pid"], c["case_id"], title, msg, now_ts)
                )

    return db.fetch_all("SELECT * FROM alerts ORDER BY acknowledged ASC, severity ASC, created_at DESC")
