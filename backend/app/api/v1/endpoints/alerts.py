from fastapi import APIRouter, Depends, HTTPException
from typing import Any, Dict, List, Optional
from app.core.database import db, now_ist, timestamp_str
from app.core.security import get_current_user
from app.schemas.payloads import NotificationDispatchTriggerRequest
from app.services.sla_and_alerts import sync_and_fetch_live_alerts
from app.services.notification_service import (
    broadcast_alert_notifications,
    list_dispatches,
    dispatch_single_notification
)

router = APIRouter()

@router.get("/")
def list_live_alerts(alert_type: Optional[str] = None, unacknowledged_only: bool = False):
    alerts = sync_and_fetch_live_alerts()
    if alert_type and alert_type != "ALL":
        alerts = [a for a in alerts if a["alert_type"] == alert_type]
    if unacknowledged_only:
        alerts = [a for a in alerts if a["acknowledged"] == 0]
    return {
        "count": len(alerts),
        "unacknowledged_count": sum(1 for a in alerts if a["acknowledged"] == 0),
        "items": [
            {
                **a,
                "id": a["alert_id"],
                "type": a["alert_type"],
                "caseId": a["case_id"],
                "timestamp": a["created_at"],
                "acknowledged": bool(a["acknowledged"])
            }
            for a in alerts
        ]
    }

# ============================================================================
# Feature 6: Email & SMS Notification Dispatches
# ============================================================================

@router.get("/dispatches")
def list_notification_dispatches(
    case_id: Optional[str] = None,
    channel: Optional[str] = None,
    delivery_status: Optional[str] = None,
    limit: int = 50
):
    """Fetches the relational dispatch log of all multi-channel (SMS & Email) officer notifications."""
    dispatches = list_dispatches(case_id=case_id, channel=channel, delivery_status=delivery_status, limit=limit)
    return {
        "count": len(dispatches),
        "items": dispatches
    }

@router.post("/{alert_id}/dispatch")
def trigger_alert_dispatch(
    alert_id: str,
    payload: Optional[NotificationDispatchTriggerRequest] = None,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Forces immediate multi-channel (Official Email & CDAC/NIC SMS) broadcast for a specific alert."""
    alert = db.fetch_one("SELECT * FROM alerts WHERE alert_id = ?", (alert_id.strip(),))
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found")

    dispatches = broadcast_alert_notifications(alert)
    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="ALERT_MANUAL_DISPATCH",
        target_entity=alert_id.strip(),
        details=f"Dispatched {len(dispatches)} multi-channel alerts (SMS/Email) for {alert_id}."
    )
    return {
        "status": "DISPATCHED",
        "alert_id": alert_id.strip(),
        "dispatches_count": len(dispatches),
        "dispatches": dispatches
    }

@router.post("/broadcast-all")
def broadcast_all_critical_alerts(
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Broadcasts Email & SMS notifications for all active critical and high alerts."""
    alerts = sync_and_fetch_live_alerts()
    critical_alerts = [a for a in alerts if a["acknowledged"] == 0 and a["severity"] in ("CRITICAL", "HIGH")]
    all_dispatches = []
    for ca in critical_alerts[:10]:
        d = broadcast_alert_notifications(ca)
        all_dispatches.extend(d)

    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="ALERTS_BULK_DISPATCH",
        target_entity="CRITICAL_ALERTS",
        details=f"Broadcast {len(all_dispatches)} multi-channel alerts across {len(critical_alerts)} active alerts."
    )
    return {
        "status": "BROADCAST_COMPLETED",
        "alerts_targeted": len(critical_alerts),
        "dispatches_generated": len(all_dispatches)
    }

@router.post("/{alert_id}/acknowledge")
def acknowledge_single_alert(alert_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    existing = db.fetch_one("SELECT * FROM alerts WHERE alert_id = ?", (alert_id.strip(),))
    if not existing:
        raise HTTPException(status_code=404, detail="Alert ID not found")
    new_val = 0 if existing["acknowledged"] == 1 else 1
    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)
    db.execute(
        "UPDATE alerts SET acknowledged = ?, acknowledged_by = ?, acknowledged_at = ? WHERE alert_id = ?",
        (new_val, current_user["email"] if new_val else None, curr_ts if new_val else None, alert_id.strip())
    )
    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="ALERT_ACKNOWLEDGED",
        target_entity=alert_id.strip(),
        details=f"Toggled alert acknowledgement={bool(new_val)} on {alert_id}"
    )
    return {"status": "updated", "alert_id": alert_id.strip(), "acknowledged": bool(new_val)}

@router.post("/acknowledge-all")
def acknowledge_all_alerts(current_user: Dict[str, Any] = Depends(get_current_user)):
    curr_ts = timestamp_str(0, now_ist().hour, now_ist().minute)
    db.execute(
        "UPDATE alerts SET acknowledged = 1, acknowledged_by = ?, acknowledged_at = ? WHERE acknowledged = 0",
        (current_user["email"], curr_ts)
    )
    db.log_audit(
        actor_email=current_user["email"],
        actor_role=current_user["role"],
        actor_bureau=current_user["bureau_code"],
        event_type="ALERTS_ACKNOWLEDGE_ALL",
        target_entity="ALL_ALERTS",
        details="Acknowledged all active SLA and cross-bureau overlap notifications."
    )
    return {"status": "all_acknowledged"}
