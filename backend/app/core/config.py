import os
from typing import List

class Settings:
    PROJECT_NAME: str = "AICTE Integrated Institution Complaint Monitoring & Status Dashboard (IICM-SD)"
    VERSION: str = "3.0.0"
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{os.path.abspath(os.path.join(os.path.dirname(__file__), '../../aicte_iicm.db'))}"
    )
    JWT_SECRET: str = os.getenv("JWT_SECRET", "aicte-mic-iicm-production-hmac-sha256-secret-2026")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = int(os.getenv("JWT_EXPIRY_HOURS", "24"))

    # Tuned fuzzy threshold (78.0%) to prevent false-positive institution matches
    OVERLAP_FUZZY_THRESHOLD: float = float(os.getenv("OVERLAP_FUZZY_THRESHOLD", "78.0"))
    CONTENT_OVERLAP_THRESHOLD: float = float(os.getenv("CONTENT_OVERLAP_THRESHOLD", "55.0"))

    SLA_DEFAULT_DAYS: int = int(os.getenv("SLA_DEFAULT_DAYS", "30"))
    SLA_WARNING_DAYS: int = int(os.getenv("SLA_WARNING_DAYS", "5"))
    PENDING_REMINDER_DAYS: int = int(os.getenv("PENDING_REMINDER_DAYS", "7"))

    UPLOAD_DIR: str = os.getenv(
        "UPLOAD_DIR",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../uploads"))
    )
    MAX_UPLOAD_BYTES: int = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))) # 10 MB

    @property
    def allowed_origins(self) -> List[str]:
        raw = os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:8080,http://127.0.0.1:8080,http://localhost:3000,http://localhost:5173,http://localhost:8000"
        )
        return [o.strip() for o in raw.split(",") if o.strip()]

settings = Settings()
