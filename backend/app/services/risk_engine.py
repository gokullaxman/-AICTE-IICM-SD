"""
AICTE IICM-SD Institution Risk Scoring Engine
============================================
Calculates institutional compliance risk scores (0-100) and risk tiers (CRITICAL, HIGH, MEDIUM, LOW)
based on multi-factor regulatory telemetry:
1. Active & historical complaint volume.
2. Category severity weighting (Anti-Ragging, Ghost Faculty, Unapproved Intake, Certificate Retention).
3. Cross-bureau overlap contagion across multiple AICTE directorates.
4. SLA timeline breach frequency and escalation depth.
"""
from typing import Any, Dict, List, Optional
from app.core.database import db
from app.services.sla_and_alerts import evaluate_case_sla, get_sla_config_map

CATEGORY_RISK_WEIGHTS: Dict[str, float] = {
    "Hostel & Campus Safety / Ragging": 35.0,
    "Fake Faculty / Biometric Attendance": 28.0,
    "Unapproved Intake / Excess Admission": 25.0,
    "Original Certificate Withholding": 20.0,
    "Fee Refund & Overcharging": 16.0,
    "Faculty Salary & Qualification Norms": 16.0,
    "Infrastructure & Lab Deficit": 12.0
}

def calculate_institution_risk(pid: str, all_cases: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    inst = db.fetch_one("SELECT * FROM institutions WHERE pid = ?", (pid.strip(),))
    if not inst:
        return {
            "pid": pid,
            "risk_score": 0.0,
            "risk_tier": "LOW",
            "active_cases": 0,
            "breached_cases": 0,
            "bureaus": [],
            "factors": []
        }

    if all_cases is None:
        inst_cases = db.fetch_all("SELECT * FROM complaint_cases WHERE pid = ?", (pid.strip(),))
    else:
        inst_cases = [c for c in all_cases if c["pid"] == pid]

    sla_map = get_sla_config_map()
    active_cases = [c for c in inst_cases if c.get("status") != "Disposed"]
    bureaus = sorted(list({c["bureau"] for c in active_cases if c.get("bureau")}))

    score = 0.0
    factors = []

    # 1. Volume Factor
    if active_cases:
        vol_score = len(active_cases) * 10.0
        score += vol_score
        factors.append(f"{len(active_cases)} active regulatory complaints (+{vol_score:.0f} pts)")

    # 2. Category Severity Factor
    highest_severity_cat = None
    highest_severity_weight = 0.0
    for c in active_cases:
        cat = c.get("category", "")
        weight = CATEGORY_RISK_WEIGHTS.get(cat, 10.0)
        score += weight * 0.75
        if weight > highest_severity_weight:
            highest_severity_weight = weight
            highest_severity_cat = cat

    if highest_severity_cat:
        factors.append(f"High-severity grievance: {highest_severity_cat} (+{highest_severity_weight * 0.75:.1f} pts)")

    # 3. Cross-Bureau Overlap Contagion
    if len(bureaus) >= 3:
        score += 35.0
        factors.append(f"Severe cross-bureau overlap spanning 3+ bureaus ({', '.join(bureaus)}) (+35 pts)")
    elif len(bureaus) == 2:
        score += 22.0
        factors.append(f"Cross-bureau overlap spanning 2 bureaus ({', '.join(bureaus)}) (+22 pts)")

    if inst.get("coordination_status") == "UNRESOLVED" and len(bureaus) > 1:
        score += 15.0
        factors.append("Unresolved multi-bureau conflict without lead coordination (+15 pts)")

    # 4. SLA Timeline Breaches
    breached_count = 0
    escalated_count = 0
    for c in active_cases:
        sla = evaluate_case_sla(c, sla_map)
        if sla["state"] == "BREACHED":
            breached_count += 1
            overdue = abs(sla["days_remaining"])
            breach_pts = min(25.0, 15.0 + (overdue * 0.5))
            score += breach_pts
        if c.get("status") == "Escalated" or int(c.get("escalation_level", 0)) > 0:
            escalated_count += 1
            score += 10.0

    if breached_count > 0:
        factors.append(f"{breached_count} SLA timeline breach(es) overdue (+{breached_count * 15} pts)")
    if escalated_count > 0:
        factors.append(f"{escalated_count} case(s) escalated to higher authority (+{escalated_count * 10} pts)")

    # Normalize to 0-100 scale
    final_score = min(100.0, max(0.0, round(score, 1)))

    if final_score >= 75.0:
        tier = "CRITICAL"
        action = "Immediate Standing Hearing Committee (SHC) summons & hold on EoA intake expansion"
    elif final_score >= 50.0:
        tier = "HIGH"
        action = "Priority Joint Inspection Committee & Show-Cause Notice dispatch"
    elif final_score >= 25.0:
        tier = "MEDIUM"
        action = "Active bureau monitoring & SLA compliance reminder"
    else:
        tier = "LOW"
        action = "Routine regulatory compliance / Clean standing"

    return {
        "pid": pid,
        "name": inst["name"],
        "city": inst["city"],
        "state": inst["state"],
        "region": inst["region"],
        "approval_status": inst["approval_status"],
        "risk_score": final_score,
        "risk_tier": tier,
        "active_cases": len(active_cases),
        "total_cases": len(inst_cases),
        "breached_cases": breached_count,
        "escalated_cases": escalated_count,
        "bureaus": bureaus,
        "coordination_status": inst.get("coordination_status", "SINGLE"),
        "lead_bureau": inst.get("lead_bureau"),
        "action_hold": bool(inst.get("action_hold", 0)),
        "recommended_regulatory_action": action,
        "risk_factors": factors
    }

def get_institution_risk_leaderboard(limit: int = 50, region: Optional[str] = None, state: Optional[str] = None) -> List[Dict[str, Any]]:
    institutions = db.fetch_all("SELECT * FROM institutions")
    all_cases = db.fetch_all("SELECT * FROM complaint_cases")
    leaderboard = []
    for inst in institutions:
        if region and region != "ALL" and inst.get("region", "").lower() != region.lower():
            continue
        if state and state != "ALL" and inst.get("state", "").lower() != state.lower():
            continue
        risk_info = calculate_institution_risk(inst["pid"], all_cases)
        # Update cached risk_score and risk_tier in database
        db.execute(
            "UPDATE institutions SET risk_score = ?, risk_tier = ? WHERE pid = ?",
            (risk_info["risk_score"], risk_info["risk_tier"], inst["pid"])
        )
        leaderboard.append(risk_info)

    leaderboard.sort(key=lambda x: (x["risk_score"], x["active_cases"]), reverse=True)
    return leaderboard[:limit]

