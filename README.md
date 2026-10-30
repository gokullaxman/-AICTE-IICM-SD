# AICTE Integrated Institution Complaint Monitoring & Status Dashboard (IICM-SD v3.1)
**Ministry of Education's Innovation Cell (MIC) • All India Council for Technical Education (AICTE)**

---

## Overview

The **AICTE Integrated Institution Complaint Monitoring & Status Dashboard (IICM-SD)** is a unified regulatory oversight system designed to eliminate fragmented complaint handling across AICTE bureaus (Regulation Bureau, Public Grievance Redressal Cell, Approval Bureau, Vigilance Cell, and Regional Offices).

The system integrates:
1. **Intelligent Cross-Bureau Overlap Detection** with Jaro-Winkler entity resolution and TF-IDF content similarity.
2. **Statutory SLA Enforcement & Auto-Escalation Ladder** to prevent administrative delays.
3. **Institutional Compliance Risk Scoring (0–100)** to pinpoint repeat-violation colleges.
4. **Data Privacy Compliance (DPDP Act 2023)** with role-based personal data masking.
5. **Secure Gemini AI Chatbot Integration (IICM Assistant)** grounded in live database evidence.

---

## Features & Regulatory Capabilities

### 1. Lead-Bureau Designation & Conflicting Action "Hold"
- Prevents uncoordinated or conflicting unilateral actions when an institution faces concurrent complaints across multiple bureaus.
- The Competent Authority can designate a **Lead Bureau** (e.g., Regulation Bureau) and place non-lead actions on **Action Hold**, preventing premature closure or contradictory directives.

### 2. Auto-Escalation Ladder on SLA Breaches
- Dynamically computes statutory SLA targets (e.g., Ragging: 7 days, Withholding Certificates: 15 days, General: 30 days).
- Automatically triggers multi-tier escalation when deadlines are exceeded:
  - **Level 1 (SLA Breached)**: Promoted to Bureau Head / Advisor.
  - **Level 2 (Breached + 7 days)**: Promoted to Chief Vigilance Officer / Coordinating Advisor.
  - **Level 3 (Breached + 14 days)**: Promoted to Competent Authority / Member Secretary / Chairman Desk.

### 3. Single-Window Intake API & Webhook
- `POST /api/v1/cases/webhook` accepts incoming grievances from the centralized AICTE Grievance Portal or Ministry of Education feeds.
- Validates payload HMAC authentication, executes deduplication check, matches approved institution PID, and routes to the appropriate bureau.

### 4. Multilingual Entity Matching (Hindi, Tamil, English)
- Resolves regional language grievances submitted in Hindi (Devanagari) and Tamil (Tamil script) by expanding phonetic variants and keywords into normalized AICTE canonical entities.

### 5. Institutional Compliance Risk Engine & Leaderboard
- Computes an objective **Compliance Risk Score (0–100)** and risk tier (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`) using weighted multi-factor scoring:
  - Active complaint volume (+10 pts per case)
  - Grievance gravity (+25 pts for Ragging/Fake Faculty)
  - Cross-bureau overlap (+35 pts for 3+ bureaus)
  - Uncoordinated overlap conflict (+15 pts)
  - Overdue SLA breaches (+15 pts per breach)
  - Escalation ladder tier (+10 pts per level)

### 6. Email & SMS Notification Dispatch System
- `POST /api/v1/alerts/{id}/dispatch` and `/broadcast-all` generate mock multi-channel dispatches (Email & SMS) to assigned officers, bureau heads, and regional directors with full delivery timestamps.

### 7. Tamper-Evident Cryptographic Audit Log (Hash-Chained)
- Every workflow mutation, status update, transfer, unmasking request, and coordination action is logged with an immutable SHA-256 cryptographic hash chaining to the preceding entry:
  $$\text{Hash}_n = \text{SHA256}(\text{Index} \parallel \text{Timestamp} \parallel \text{User} \parallel \text{Action} \parallel \text{Hash}_{n-1})$$
- `/api/v1/reports/audit-verify` validates the entire hash chain from the genesis block, detecting any unauthorized database tampering.

### 8. Complainant Personal Data Masking (DPDP Act 2023)
- Under the Digital Personal Data Protection (DPDP) Act 2023, complainant names, emails, and phone numbers are dynamically masked for non-owning bureaus and unauthenticated callers.
- Authorized officers requiring unmasked data for statutory hearings must submit a spoken justification through `POST /api/v1/cases/{id}/unmask-pii`, creating an immutable audit entry.

---

## Secure Gemini AI Chatbot Integration (IICM Assistant)

The dashboard includes **IICM Assistant**, a lightweight, production-grade AI assistant powered by Google's official Gemini API (`google-genai` Python SDK).

### Frontend UI & Experience
- **Floating Launcher**: Clean, fixed button at the bottom-right corner with online pulsing indicator and keyboard shortcut (`⌘J` / `Ctrl+J`).
- **Compact Assistant Panel**: Modal-free slide-up dialog adhering to Indian government dignity, deep navy palette (`#0B1F3A`), and dark/light theme support.
- **Interactive Capabilities**:
  - Quick-prompt starter chips ("What does this dashboard do?", "Explain SLA escalation ladder", etc.)
  - Auto-scrolling conversation area with Markdown formatting (`**bold**`, `*italic*`, `` `code` ``, lists)
  - Typing animation indicator ("Analyzing...")
  - Character counter (0 / 2000 chars)
  - Error banner with inline "Retry" button
  - Clear conversation button and Esc shortcut to minimize
  - Reference source tags (e.g., `Sources: Case Docket AICTE-RB-0409`)

### Backend Architecture & Security Controls
- **Endpoint**: `POST /api/v1/chat/message`
- **Official SDK**: Built on `google-genai` v2.34.0 using `client.aio.models.generate_content`.
- **Default Model**: `gemini-3.1-flash-lite` (fast, cost-effective, high-throughput).
- **Authentication**: Protected by FastAPI `Depends(get_current_user)` enforcing valid HS256 JWT bearer tokens.
- **Sliding-Window Rate Limiting**: Enforces a strict limit (default: 30 requests/minute per officer or IP address) returning `HTTP 429 Too Many Requests`.
- **Strict Read-Only Persona**: The assistant provides advisory and explanatory support; it strictly refuses unilateral status mutations, deletions, or transfers, guiding the officer to perform authorized actions in the dashboard.
- **DPDP Act 2023 Data Protection**: All retrieved database evidence is pre-filtered through `apply_pii_masking`. Personal contact details remain masked (`***`) in prompt context, preventing accidental leaks.
- **Anti-Prompt Injection Defense**: Database evidence is demarcated within strict security boundaries (`=== BEGIN AUTHORIZED DATABASE EVIDENCE (UNTRUSTED DATA) ===`). Any adversarial instructions or role-override directives embedded in user input or complaint text are neutralized.
- **Zero API Key Leakage**: The Gemini API key is managed strictly on the server and is never returned in client payloads, error logs, or audit trails.

### Conversational Capabilities & Live Data Grounding
1. **Case Docket Inquiries**:
   - Query: *"What is the status of AICTE-RB-0409 and who is the officer?"*
   - Returns: Owning bureau, assigned officer, category, current status, live SLA countdown, escalation tier, hold status, and recent workflow history.
2. **Institution 360 Profiles**:
   - Query: *"Tell me about Velammal Institute (PID 1-6111471). Does it have overlaps?"*
   - Returns: Approved intake, location, compliance risk score, risk tier, active complaints across bureaus, lead bureau, and action hold status.
3. **Risk Engine Leaderboard**:
   - Query: *"Which institutions have the highest risk scores in AICTE?"*
   - Returns: Ranked top-5 institutions with compliance risk scores (0–100), active cases, and primary violation drivers.
4. **Live SLA & Overlap Alerts**:
   - Query: *"What alerts and SLA breaches are active right now?"*
   - Returns: Summary count of critical/high alerts and breakdown of urgent unacknowledged notices.
5. **Multi-Bureau Overlaps**:
   - Query: *"Which institutions currently have overlapping complaints across bureaus?"*
   - Returns: Institutions requiring Section 10(1) coordination, active bureaus involved, and lead bureau designations.
6. **Executive MIS Metrics**:
   - Query: *"Give me the overall dashboard summary and disposal rate."*
   - Returns: Total dockets, pending, under process, escalated, disposed, and overall disposal rate percentage.
7. **Anti-Hallucination Fallback**:
   - Querying an unknown case ID (e.g., `AICTE-ZZ-9999`) or PID explicitly returns a verified statement that no record was found in the database.

---

## Configuration & Environment Variables

Create a `.env` file in the root directory (or use `.env.example`):

```ini
# Application Configuration
APP_ENV=development
PORT=8000
DATABASE_URL=sqlite:///backend/aicte_iicm.db

# JWT & Security Configuration
JWT_SECRET=aicte-mic-iicm-production-hmac-sha256-secret-2026
JWT_EXPIRY_HOURS=24
ALLOWED_ORIGINS=http://localhost:8080,http://127.0.0.1:8080,http://localhost:8000,http://localhost:3000

# Overlap & SLA Thresholds
OVERLAP_FUZZY_THRESHOLD=78.0
CONTENT_OVERLAP_THRESHOLD=55.0
SLA_DEFAULT_DAYS=30
SLA_WARNING_DAYS=5
PENDING_REMINDER_DAYS=7

# Gemini AI Chatbot Settings (Official google-genai SDK)
GEMINI_API_KEY=YOUR_GEMINI_API_KEY_HERE
GEMINI_MODEL=gemini-3.1-flash-lite
GEMINI_CHAT_ENABLED=true
GEMINI_REQUEST_TIMEOUT=30
GEMINI_MAX_INPUT_CHARS=2000
GEMINI_MAX_HISTORY_MESSAGES=10
GEMINI_MAX_OUTPUT_TOKENS=1024
GEMINI_RATE_LIMIT_PER_MINUTE=30
```

---

## Quick Start & Verification

### 1. Run Server Locally
```bash
python run.py
```
- **Web Application**: `http://localhost:8000`
- **FastAPI Interactive Docs**: `http://localhost:8000/docs`
- **System Health**: `http://localhost:8000/health`

### 2. Demo User Credentials
| User / Officer | Email | Role | Bureau Scope | Demo Password |
| :--- | :--- | :--- | :--- | :--- |
| **Member Secretary Desk** | `admin@aicte-india.org` | `COMPETENT_AUTHORITY` | HQ (All Bureaus) | `Aicte@2026` |
| **Dr. Ananya Nair** | `rb.nodal@aicte-india.org` | `BUREAU_OFFICER` | Regulation Bureau (RB) | `RbOfficer@2026` |
| **Shri V. K. Sharma** | `pgrc.nodal@aicte-india.org` | `BUREAU_OFFICER` | PGRC | `PgrcOfficer@2026` |
| **Shri M. S. Gill** | `ab.nodal@aicte-india.org` | `BUREAU_OFFICER` | Approval Bureau (AB) | `AbOfficer@2026` |
| **Shri D. P. Singh** | `vc.nodal@aicte-india.org` | `BUREAU_OFFICER` | Vigilance Cell (VC) | `VcOfficer@2026` |
| **Dr. M. Sundaresan** | `sro.chennai@aicte-india.org` | `REGIONAL_OFFICER` | Southern Region (RO) | `RoOfficer@2026` |

---

## Automated Test Suite (55 Passing Tests)

The test suite provides 100% automated test coverage across all features:

```bash
cd backend
python -m pytest tests/test_chat_phase1.py tests/test_chat_phase2.py tests/test_overlap_and_api.py -v
```

### Test Suite Summary:
- **`tests/test_chat_phase1.py` (15 Tests)**:
  - Valid chat request/response flow.
  - Input validation (empty message, oversized input, malformed history, unsupported roles).
  - Unconfigured API key graceful fallback.
  - Provider rate limits (429), timeouts (504), network errors (502), and empty model outputs.
  - Per-user/IP sliding window rate limiting.
  - JWT authentication requirement (401).
  - Zero API key or internal exception leakage in client responses.
- **`tests/test_chat_phase2.py` (15 Tests)**:
  - Entity extraction (case IDs, PIDs, institution names/aliases).
  - Grounded case docket retrieval with SLA countdown and recent workflow history.
  - DPDP Act 2023 complainant PII masking on cross-bureau inquiries.
  - Unknown case handling without hallucination.
  - Institution 360 profile context with compliance risk scores and overlap detection.
  - Institutional risk leaderboard inquiries.
  - Live alerts and SLA watchlist inquiries.
  - Multi-bureau overlap engine summaries.
  - Executive MIS summary metrics.
  - Prompt injection defense and instruction override neutralization.
  - Read-only mutation refusal with dashboard action guidance.
- **`tests/test_overlap_and_api.py` (25 Tests)**:
  - 78% Jaro-Winkler entity resolution and false-positive prevention.
  - Content TF-IDF and category relatedness matrix.
  - Lead-bureau designation and unilateral action hold enforcement.
  - SLA breach auto-escalation ladder.
  - Single-window webhook intake.
  - Multilingual matching (Hindi & Tamil).
  - Institutional compliance risk scoring.
  - Email & SMS notification dispatch.
  - Tamper-evident cryptographic audit log verification.
  - DPDP Act PII masking and authorized unmasking audit trail.

**Total**: **55 passed in 6.10s (100% pass rate)**.

---

## Deployment with Docker Compose

```bash
cp .env.example .env
docker compose up --build -d
```
- **Web Application**: `http://localhost:8080`
- **FastAPI Backend**: `http://localhost:8000`
- **PostgreSQL Database**: `localhost:5432`

---

## Deployment to Vercel (Production Ready)

The application is structured for instant zero-config deployment to [Vercel](https://vercel.com):

### Architecture on Vercel
1. **Edge CDN Frontend**: The single-file government-grade dashboard is served directly from `public/index.html` across Vercel's global CDN (0ms cold start).
2. **Serverless REST API**: Backend endpoints (`/api/v1/*`), health check (`/health`), and OpenAPI documentation (`/docs`) are executed via Python Serverless Functions through `api/index.py` configured in `vercel.json`.
3. **Database Adaptation**: Automatically detects the serverless environment (`VERCEL=1`) and initializes the SQLite relational database in writable `/tmp/aicte_iicm.db` on cold start. Alternatively, configure a cloud PostgreSQL connection string via `DATABASE_URL` (e.g., Neon, Supabase, Vercel Postgres).
4. **CORS & Preview URLs**: Dynamic origin regex automatically supports all `https://*.vercel.app` preview and production URLs without manual configuration.
5. **Auto-Connecting Frontend**: The frontend automatically resolves `window.location.origin + '/api/v1'` on deployed domains.

### Method 1: Git Integration (Recommended)
1. Push this repository to GitHub:
   ```bash
   git push origin main
   ```
2. Navigate to [vercel.com/new](https://vercel.com/new) and import the repository.
3. Keep the default settings (Framework: **Other**, Root Directory: `./`).
4. In **Environment Variables**, set:
   - `GEMINI_API_KEY`: *(Required for AI Chatbot)* Your Google Gemini API key.
   - `JWT_SECRET`: *(Recommended)* A secure random string for signing officer session tokens.
   - `DATABASE_URL`: *(Optional)* Remote PostgreSQL connection string (if using external DB). If omitted, serverless SQLite is used automatically.
5. Click **Deploy**.

### Method 2: Vercel CLI
```bash
# Install Vercel CLI
npm install -g vercel

# Deploy preview
vercel

# Deploy to production
vercel --prod
```
