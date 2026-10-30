import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List
from fastapi import Depends, HTTPException, Header, status
from app.core.config import settings

def hash_password(password: str, salt: str = "aicte_mic_salt_2026") -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"pbkdf2_sha256${salt}${dk.hex()}"

def verify_password(plain_password: str, stored_hash: str) -> bool:
    if not stored_hash or "$" not in stored_hash:
        return False
    parts = stored_hash.split("$")
    if len(parts) != 3:
        return False
    _, salt, _ = parts
    expected = hash_password(plain_password, salt)
    return hmac.compare_digest(expected, stored_hash)

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _b64url_decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))

def create_jwt_token(payload: Dict[str, Any], expires_hours: Optional[int] = None) -> str:
    exp_hours = expires_hours if expires_hours is not None else settings.JWT_EXPIRY_HOURS
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=exp_hours)
    header = {"alg": settings.JWT_ALGORITHM, "typ": "JWT"}
    body = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp())
    }
    h_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    b_b64 = _b64url_encode(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h_b64}.{b_b64}".encode("ascii")
    sig = hmac.new(settings.JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{h_b64}.{b_b64}.{_b64url_encode(sig)}"

def decode_jwt_token(token: str) -> Dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid token format")
        h_b64, b_b64, s_b64 = parts
        signing_input = f"{h_b64}.{b_b64}".encode("ascii")
        expected_sig = hmac.new(settings.JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
        actual_sig = _b64url_decode(s_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise ValueError("Invalid JWT signature")
        payload = json.loads(_b64url_decode(b_b64).decode("utf-8"))
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if payload.get("exp", 0) < now_ts:
            raise ValueError("JWT token has expired")
        return payload
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(exc)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

def get_current_user(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    return decode_jwt_token(token)

def get_optional_current_user(authorization: Optional[str] = Header(default=None)) -> Optional[Dict[str, Any]]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        token = authorization.split(" ", 1)[1].strip()
        return decode_jwt_token(token)
    except Exception:
        return None

def check_bureau_permission(user: Dict[str, Any], target_bureau: str, allow_cross_bureau: bool = False) -> None:
    """
    Enforces per-bureau role-based access control (RBAC).
    - COMPETENT_AUTHORITY has full access across all bureaus.
    - BUREAU_OFFICER and REGIONAL_OFFICER can mutate cases assigned to their own bureau,
      or participate in cross-bureau operations when allow_cross_bureau=True.
    """
    role = user.get("role", "")
    user_bureau = user.get("bureau_code", "")
    if role == "COMPETENT_AUTHORITY":
        return
    if allow_cross_bureau:
        return
    if user_bureau != target_bureau:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"RBAC Permission Denied: Officer from bureau '{user_bureau}' ({role}) "
                f"cannot directly modify a case owned by '{target_bureau}' without transfer or joint coordination."
            ),
        )

# ============================================================================
# DPDP Act 2023 Complainant PII Masking Utilities
# ============================================================================

def mask_name(name: str) -> str:
    """Masks personal names in compliance with DPDP Act 2023 (e.g., 'Ramesh Kumar' -> 'R***** K****')."""
    if not name:
        return ""
    name = name.strip()
    # If it is a group/official reference, keep category clear
    if name.lower().startswith("batch of") or "committee" in name.lower() or "directorate" in name.lower() or "reference" in name.lower():
        return name
    parts = name.split()
    masked = []
    for p in parts:
        if len(p) <= 2:
            masked.append(p)
        else:
            masked.append(p[0] + "*" * (len(p) - 1))
    return " ".join(masked)

def mask_email(email: str) -> str:
    """Masks email address (e.g. 'ramesh.kumar@gmail.com' -> 'r****r@gmail.com')."""
    if not email or "@" not in email:
        return email or ""
    user, domain = email.split("@", 1)
    if len(user) <= 2:
        masked_user = user[0] + "*"
    else:
        masked_user = user[0] + "*" * (len(user) - 2) + user[-1]
    return f"{masked_user}@{domain}"

def mask_phone(phone: str) -> str:
    """Masks phone numbers (e.g. '+91 9876543210' -> '+91 98*****210')."""
    if not phone:
        return ""
    p = phone.strip()
    if len(p) < 6:
        return "***"
    prefix_len = 4 if p.startswith("+91") else 2
    return p[:prefix_len] + "*" * (len(p) - prefix_len - 3) + p[-3:]

def can_view_pii(user: Optional[Dict[str, Any]], case_bureau: str) -> bool:
    """
    Checks if requesting user has full unmasked DPDP clearance.
    - COMPETENT_AUTHORITY: Full unmasked view across all complaints.
    - BUREAU_OFFICER: Full unmasked view for complaints owned by their own bureau.
    - External / Anonymous / Non-owning bureau without unmask clearance: Masked.
    """
    if not user:
        return False
    role = user.get("role", "")
    if role == "COMPETENT_AUTHORITY":
        return True
    user_bureau = user.get("bureau_code", "")
    if role in ("BUREAU_OFFICER", "REGIONAL_OFFICER") and user_bureau == case_bureau:
        return True
    return False

def apply_pii_masking(case_data: Dict[str, Any], current_user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Applies role-based dynamic masking to complainant PII according to DPDP Act 2023."""
    bureau = case_data.get("bureau", "")
    if can_view_pii(current_user, bureau):
        return {
            **case_data,
            "is_pii_masked": False,
            "pii_clearance": "GRANTED"
        }

    raw_name = case_data.get("complainant", "")
    raw_email = case_data.get("complainant_email", "")
    raw_phone = case_data.get("complainant_phone", "")

    return {
        **case_data,
        "complainant": mask_name(raw_name),
        "complainant_email": mask_email(raw_email),
        "complainant_phone": mask_phone(raw_phone),
        "raw_complainant_name": None,
        "is_pii_masked": True,
        "pii_clearance": "RESTRICTED_DPDP_2023",
        "pii_mask_notice": "Personal data masked per Digital Personal Data Protection (DPDP) Act 2023. Authorized officers can request lawful unmasking via /api/v1/cases/{case_id}/unmask-pii with justification."
    }
