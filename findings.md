# AICTE IICM-SD — Repository Inspection & Architecture Findings

## Date: 2026-10-30

### 1. Application Initialization & Router Registration
- **Backend Entry**: `backend/app/main.py`.
- **FastAPI Lifespan**: `db.initialize_and_seed()` runs on startup, setting up tables and loading reference data.
- **Routers**:
  - `/api/v1/auth` -> `auth.router`
  - `/api/v1/institutions` -> `institutions.router`
  - `/api/v1/cases` -> `cases.router`
  - `/api/v1/overlaps` -> `overlaps.router`
  - `/api/v1/alerts` -> `alerts.router`
  - `/api/v1/reports` -> `reports.router`
  - `/api/v1/chat` -> `chat.router` (`POST /api/v1/chat/message`)
- **Frontend Serving**: `GET /` serves `frontend/public/index.html`.
- **Health Check**: `GET /health` reports database status, engine type, institutions count, and cases count.

### 2. Frontend Entry Points & Structure
- **Comparison**: `frontend/public/index.html` and `aicte_complaint_monitoring_dashboard.html` are exact identical copies (both synchronized).
- **Actual Web Entry**: `frontend/public/index.html` is the primary file served by FastAPI at `/` and by Nginx in Docker at `/usr/share/nginx/html/index.html`.
- **Styling & Stack**: Self-contained single-file frontend using Tailwind CSS CDN, Chart.js CDN, Google Fonts (Inter + JetBrains Mono), and Lucide-style inline SVGs.
- **Theme**: CSS design tokens (`--bg-page`, `--bg-surface`, `--border-default`, etc.) with light/dark theme toggle persisted in `localStorage`.
- **API Base URL**: Configurable in JavaScript `apiBaseUrl = 'http://localhost:8000/api/v1'`, dynamic health probe on startup (`/health`).
- **Token Management**: `jwtToken` stored in memory, sent as `Authorization: Bearer <jwtToken>` on mutating calls.

### 3. Authentication & RBAC
- **Token Format**: HS256 JWT generated in `backend/app/core/security.py` using HMAC-SHA256 (`JWT_SECRET`).
- **User Claims**:
  - `sub`: User ID (e.g. `USR-HQ-01`, `USR-RB-01`)
  - `email`: Official email (e.g. `admin@aicte-india.org`, `rb.nodal@aicte-india.org`)
  - `role`: `COMPETENT_AUTHORITY`, `BUREAU_OFFICER`, `REGIONAL_OFFICER`
  - `bureau_code`: `HQ`, `RB`, `PGRC`, `AB`, `VC`, `RO`
  - `region_scope`: Geographic jurisdiction (e.g. `ALL`, `Southern`, etc.)
- **Dependencies**: `get_current_user` (strict authenticated) and `get_optional_current_user` (graceful fallback).

### 4. Complaint Schemas, Authorization & DPDP PII Masking
- **Complainant PII Protection**: Strict compliance with Digital Personal Data Protection (DPDP) Act 2023 in `backend/app/core/security.py`:
  - `mask_name`: e.g. `R***** K****`
  - `mask_email`: e.g. `r***@g*****.com`
  - `mask_phone`: e.g. `+91 98*** **321`
  - `apply_pii_masking`: Masking enforced dynamically unless user is `COMPETENT_AUTHORITY` or `BUREAU_OFFICER` for their own bureau.
  - Statutory unmasking requires justification logged to SHA-256 audit ledger (`/api/v1/cases/{case_id}/unmask-pii`).
- **Section 10(1) Action Hold**: When an institution is under active joint coordination, unilateral mutations by non-lead bureaus are blocked with `HTTP 409 Conflict`.

### 5. Services & Engines
- **Overlap Engine**: `CrossBureauOverlapEngine` in `overlap_engine.py` (Jaro-Winkler 78%, multilingual concept expansion for Hindi/Tamil, TF-IDF cosine similarity).
- **Risk Engine**: `calculate_institution_risk` in `risk_engine.py` (0-100 score, `CRITICAL`, `HIGH`, `MEDIUM`, `LOW` tiers).
- **SLA & Escalation Engine**: `sla_and_alerts.py` (4-tier escalation ladder, automated promotions).
- **Notification Service**: `notification_service.py` (NIC SMS & AICTE SMTP dispatches).
- **Audit Verification**: `database.py` (SHA-256 Merkle hash chain from genesis block).

### 6. Gemini Model Selection & Performance Findings
- Tested Google GenAI models with the configured API key:
  - `gemini-3.8-flash`: Valid, but subjected to upstream server 503/504 high-demand spikes.
  - `gemini-3.1-flash-lite`: Highly responsive (average response time: 1.4s), consistent 200 OK throughput, official production-ready model.
  - Configured `http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1))` in client initialization to avoid client-side hangs during transient upstream spikes.

### 7. Phase 1 & Phase 2 Chatbot Architecture Findings
- **Frontend Architecture**: Embedded floating widget `#aiChatLauncherContainer` and `#aiChatPanel` into `frontend/public/index.html` (and synchronized root copy `aicte_complaint_monitoring_dashboard.html`). Uses existing CSS design tokens, responsive layout, keyboard shortcut (`⌘J`/`Ctrl+J`), and clean HTML sanitization (`sanitizeChatHtml`).
- **Grounding Architecture**: Implemented `DashboardContextRetriever` in `backend/app/services/gemini_chat_service.py`. It dynamically inspects user inquiries for:
  - Specific Case IDs (`AICTE-[A-Z]+-[0-9]+`) -> Retrieves case docket, SLA countdown, and masks complainant PII.
  - Institution PIDs (`1-[0-9]{5,11}`) or Distinctive Names -> Retrieves 360 profile, risk score (0-100), active cases, and overlap status.
  - Risk Leaderboard Queries -> Retrieves top-5 high-risk institutions.
  - Live Alerts Queries -> Retrieves active alerts and SLA watchlist.
  - Overlap Queries -> Retrieves multi-bureau overlapping institutions and lead bureau coordination status.
  - MIS Summary Queries -> Retrieves executive summary statistics.
- **Security & DPDP Compliance**: Complainant data is pre-masked in Python before inclusion in model context. Anti-prompt injection demarcations treat database text strictly as untrusted data.
- **Read-Only Persona**: Enforced through system instructions; assistant refuses direct mutation requests and guides officers on dashboard workflow actions.
- **Test Results**: 55 automated tests passing 100% across Phase 1 (`test_chat_phase1.py`), Phase 2 (`test_chat_phase2.py`), and platform regression (`test_overlap_and_api.py`).
