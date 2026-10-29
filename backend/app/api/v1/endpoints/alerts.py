from fastapi import APIRouter, Depends, HTTPException
from typing import Any, Dict, Optional
from app.core.database import db, now_ist, timestamp_str
from app.core.security import get_current_user
from app.services.sla_and_alerts import sync_and_fetch_live_alerts

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
