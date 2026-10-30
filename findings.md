# AICTE IICM-SD Findings & Research Log
Date: 2026-10-30

## Technical Environment Findings
- OS: Windows
- Python: 3.13.12 with `fastapi`, `uvicorn`, `reportlab`, `rapidfuzz`, `pytest` installed.
- Database: Dual PostgreSQL + SQLite support via `RelationalDatabase` class in `backend/app/core/database.py`. Local default runs on SQLite at `backend/aicte_iicm.db`.
- Frontend: Single self-contained HTML at `frontend/public/index.html` and root `aicte_complaint_monitoring_dashboard.html`.

## Feature Architecture Analysis
1. **Lead Bureau Designation & Action Hold**:
   - Regulatory need: Multiple bureaus issuing conflicting notices to the same college causes confusion and administrative conflict.
   - Mechanism: When an institution is coordinated or marked with active overlap, a Lead Bureau is assigned. Other bureaus have an `action_hold = 1` flag active. If a non-lead officer attempts to update status, issue an order, or transfer without lead clearance, the API blocks with HTTP 409 Conflict.
2. **Auto-Escalation Ladder**:
   - Ladder tiers:
     - Level 0 (Assigned Desk Officer, 0-100% SLA)
     - Level 1 (Assistant Director / Regional Officer, SLA breached 1-7 days)
     - Level 2 (Bureau Head / Advisor, SLA breached 8-15 days)
     - Level 3 (Member Secretary / Chairman, SLA breached >15 days)
   - Auto-promotes case status to `Escalated`, updates `escalation_level`, logs audit trail, and creates high-priority alert.
3. **Complaint Intake Webhook API**:
   - Single-window endpoint: `POST /api/v1/cases/webhook` and `POST /api/v1/cases/intake`.
   - Ingests complaints from external grievance systems (CPGRAMS, state portals, web forms).
   - Validates HMAC / token header, resolves institution (exact PID, embedded PID, fuzzy name, multilingual), triggers cross-bureau overlap detection immediately, and creates complaint with receipt token.
4. **Multilingual Overlap Matching**:
   - Complaints in Indian context often come in Hindi (Devanagari) or Tamil (Tamil script) or romanized forms (Hinglish/Tanglish).
   - Cross-lingual mapping maps domain terms (fees, ragging, fake faculty, certificates, hostel) to standard category codes.
   - Text normalization strips language-specific stopwords and compares cross-lingual TF-IDF and n-grams.
5. **Institution Risk Score**:
   - Multi-factor algorithm:
     - Complaint volume factor
     - Severity weighting (Anti-ragging: 30, Ghost faculty: 25, Fee extortion: 20, etc.)
     - Cross-bureau overlap recurrence (+20)
     - SLA breach count (+15 per overdue case)
     - Non-compliance history
   - Normalizes to 0-100 score and assigns Risk Tiers: CRITICAL, HIGH, MEDIUM, LOW.
6. **Email and SMS Alerts**:
   - New table `notification_dispatches` tracking channel (EMAIL, SMS), recipient, content, status, and timestamp.
   - Real-time dispatch trigger on critical alerts (overlaps, SLA breaches, escalation promotions).
   - API to inspect dispatched notifications and simulate/send manual notifications.
7. **Tamper-Evident Hash-Chained Audit Log**:
   - Blockchain-style Merkle link: each audit row stores `prev_hash` and `entry_hash = SHA256(prev_hash + timestamp + actor + role + bureau + event + target + details)`.
   - Genesis block initialized with 64 zero characters.
   - Full cryptographic chain verification endpoint (`GET /api/v1/reports/audit-verify`) validates integrity of entire history.
8. **DPDP Act Role-Based PII Masking**:
   - Complainant name, email, phone masked dynamically for unauthorized or general bureau viewers:
     - Name: `Ramesh Kumar` -> `R***** K****`
     - Email: `ramesh.kumar@gmail.com` -> `r*****@gmail.com`
     - Phone: `+91 9876543210` -> `+91 98*****210`
   - Competent Authority and assigned Lead Officer have unmasked access.
   - Dedicated unmask API logs DPDP audit reason.
