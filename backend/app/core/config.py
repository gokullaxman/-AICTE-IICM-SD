import os
import tempfile
from typing import List

try:
    from dotenv import load_dotenv
    root_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.env"))
    backend_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.env"))
    if os.path.exists(root_env):
        load_dotenv(root_env)
    if os.path.exists(backend_env):
        load_dotenv(backend_env)
except Exception:
    pass

class Settings:
    PROJECT_NAME: str = "AICTE Integrated Institution Complaint Monitoring & Status Dashboard (IICM-SD)"
    VERSION: str = "3.1.0"
    _is_serverless: bool = bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"))
    _default_db_path: str = (
        f"sqlite:///{os.path.join(tempfile.gettempdir(), 'aicte_iicm.db').replace(os.sep, '/')}"
        if _is_serverless
        else f"sqlite:///{os.path.abspath(os.path.join(os.path.dirname(__file__), '../../aicte_iicm.db'))}"
    )
    DATABASE_URL: str = os.getenv("DATABASE_URL", _default_db_path)
    JWT_SECRET: str = os.getenv("JWT_SECRET", "aicte-mic-iicm-production-hmac-sha256-secret-2026")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = int(os.getenv("JWT_EXPIRY_HOURS", "24"))
    WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "aicte-grievance-webhook-feed-key-2026")

    # Tuned fuzzy threshold (78.0%) to prevent false-positive institution matches
    OVERLAP_FUZZY_THRESHOLD: float = float(os.getenv("OVERLAP_FUZZY_THRESHOLD", "78.0"))
    CONTENT_OVERLAP_THRESHOLD: float = float(os.getenv("CONTENT_OVERLAP_THRESHOLD", "55.0"))

    SLA_DEFAULT_DAYS: int = int(os.getenv("SLA_DEFAULT_DAYS", "30"))
    SLA_WARNING_DAYS: int = int(os.getenv("SLA_WARNING_DAYS", "5"))
    PENDING_REMINDER_DAYS: int = int(os.getenv("PENDING_REMINDER_DAYS", "7"))

    _default_upload_dir: str = (
        os.path.join(tempfile.gettempdir(), "uploads")
        if _is_serverless
        else os.path.abspath(os.path.join(os.path.dirname(__file__), "../../uploads"))
    )
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", _default_upload_dir)
    MAX_UPLOAD_BYTES: int = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))) # 10 MB

    # Gemini AI Chatbot Settings (Official google-genai SDK)
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    GEMINI_CHAT_ENABLED: bool = os.getenv("GEMINI_CHAT_ENABLED", "true").lower() in ("true", "1", "yes")
    GEMINI_REQUEST_TIMEOUT: int = int(os.getenv("GEMINI_REQUEST_TIMEOUT", "30"))
    GEMINI_MAX_INPUT_CHARS: int = int(os.getenv("GEMINI_MAX_INPUT_CHARS", "2000"))
    GEMINI_MAX_HISTORY_MESSAGES: int = int(os.getenv("GEMINI_MAX_HISTORY_MESSAGES", "10"))
    GEMINI_MAX_OUTPUT_TOKENS: int = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "1024"))
    GEMINI_RATE_LIMIT_PER_MINUTE: int = int(os.getenv("GEMINI_RATE_LIMIT_PER_MINUTE", "30"))

    @property
    def allowed_origins(self) -> List[str]:
        raw = os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:8080,http://127.0.0.1:8080,http://localhost:3000,http://localhost:5173,http://localhost:8000,https://localhost:8000"
        )
        return [o.strip() for o in raw.split(",") if o.strip()]

settings = Settings()
