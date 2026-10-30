import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from app.core.config import settings
from app.core.database import db
from app.api.v1.endpoints import auth, institutions, cases, overlaps, alerts, reports, chat

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.initialize_and_seed()
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "Production REST API for the AICTE Integrated Institution Complaint Monitoring & Status Dashboard (IICM-SD). "
        "Provides JWT RBAC authentication, relational persistence (PostgreSQL/SQLite), Jaro-Winkler + TF-IDF Cross-Bureau "
        "Overlap Detection, live SLA alert computation, file uploads, and server-side CSV/PDF MIS reporting."
    ),
    lifespan=lifespan
)

# Tightened CORS configuration reading from ALLOWED_ORIGINS (Item 14)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|.*\.vercel\.app)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication & RBAC"])
app.include_router(institutions.router, prefix="/api/v1/institutions", tags=["Centralized Institution Profiles"])
app.include_router(cases.router, prefix="/api/v1/cases", tags=["Complaint Case Tracking & Workflow"])
app.include_router(overlaps.router, prefix="/api/v1/overlaps", tags=["Cross-Bureau Overlap Engine"])
app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Live SLA & Overlap Alerts"])
app.include_router(reports.router, prefix="/api/v1/reports", tags=["MIS Reports & Exports"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["AI Chatbot (Gemini)"])

@app.get("/", include_in_schema=False)
def serve_index():
    candidates = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../public/index.html")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../frontend/public/index.html")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../public/index.html")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../frontend/public/index.html")),
        os.path.abspath(os.path.join(os.getcwd(), "public/index.html")),
        os.path.abspath(os.path.join(os.getcwd(), "frontend/public/index.html")),
        os.path.abspath(os.path.join(os.getcwd(), "aicte_complaint_monitoring_dashboard.html")),
    ]
    for cand in candidates:
        if os.path.exists(cand):
            return FileResponse(cand)
    index_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../frontend/public/index.html"))
    if not os.path.exists(index_path):
        index_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../frontend/public/index.html"))
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"status": "healthy", "service": "AICTE-IICM-SD-API", "docs": "/docs"}

@app.get("/index.html", include_in_schema=False)
def serve_index_alias():
    return serve_index()

@app.get("/aicte_complaint_monitoring_dashboard.html", include_in_schema=False)
def serve_dashboard_alias():
    return serve_index()

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)

@app.get("/health")
def health_check():
    inst_cnt = db.fetch_one("SELECT COUNT(*) AS cnt FROM institutions")["cnt"]
    case_cnt = db.fetch_one("SELECT COUNT(*) AS cnt FROM complaint_cases")["cnt"]
    return {
        "status": "healthy",
        "service": "AICTE-IICM-SD-API",
        "version": settings.VERSION,
        "database_engine": "PostgreSQL" if db.is_postgres else "SQLite3 (Relational)",
        "institutions_loaded": inst_cnt,
        "cases_loaded": case_cnt
    }
