from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Any
from app.core.database import db
from app.core.security import verify_password, create_jwt_token, get_current_user
from app.schemas.payloads import LoginRequest

router = APIRouter()

@router.post("/login")
def login_officer(payload: LoginRequest):
    user = db.fetch_one("SELECT * FROM users WHERE LOWER(email) = LOWER(?) AND is_active = 1", (payload.email.strip(),))
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid official AICTE email or password"
        )
    token_claims = {
        "sub": user["user_id"],
        "email": user["email"],
        "full_name": user["full_name"],
        "designation": user["designation"],
        "bureau_code": user["bureau_code"],
        "role": user["role"],
        "region_scope": user["region_scope"]
    }
    token = create_jwt_token(token_claims)
    db.log_audit(
        actor_email=user["email"],
        actor_role=user["role"],
        actor_bureau=user["bureau_code"],
        event_type="AUTH_LOGIN",
        target_entity=user["user_id"],
        details=f"Authenticated session started for {user['full_name']} ({user['designation']})"
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "officer": token_claims
    }

@router.get("/me")
def get_authenticated_profile(current_user: Dict[str, Any] = Depends(get_current_user)):
    return {"authenticated": True, "officer": current_user}

@router.get("/demo-accounts")
def list_demo_accounts():
    """Returns pre-configured official test credentials for evaluator role testing."""
    rows = db.fetch_all("SELECT user_id, email, full_name, designation, bureau_code, role, region_scope FROM users WHERE is_active = 1")
    default_passwords = {
        "admin@aicte-india.org": "Aicte@2026",
        "rb.nodal@aicte-india.org": "RbOfficer@2026",
        "pgrc.nodal@aicte-india.org": "PgrcOfficer@2026",
        "ab.nodal@aicte-india.org": "AbOfficer@2026",
        "vc.nodal@aicte-india.org": "VcOfficer@2026",
        "sro.chennai@aicte-india.org": "RoOfficer@2026"
    }
    return {
        "accounts": [
            {**r, "demo_password": default_passwords.get(r["email"], "Aicte@2026")}
            for r in rows
        ]
    }

@router.get("/audit-logs")
def list_audit_logs(limit: int = 50, current_user: Dict[str, Any] = Depends(get_current_user)):
    logs = db.fetch_all("SELECT * FROM audit_logs ORDER BY audit_id DESC LIMIT ?", (min(limit, 200),))
    return {"count": len(logs), "items": logs}
