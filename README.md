# AICTE Integrated Institution Complaint Monitoring & Status Dashboard (IICM-SD v3.0)
**Ministry of Education's Innovation Cell (MIC) • All India Council for Technical Education (AICTE)**

## Architecture & Engineering Upgrades (v3.0 Production Build)

### 1. Core Architecture & Database Layer (`PostgreSQL 16` + `SQLite3` Fallback)
- **Zero In-Memory Stubs**: All endpoints read and write directly to a 10-table relational database (`bureaus`, `users`, `sla_configs`, `institutions`, `complaint_cases`, `case_links`, `workflow_history`, `uploaded_documents`, `alerts`, `audit_logs`) configured via `DATABASE_URL` in `backend/app/core/database.py`.
- **Dynamic Relative Timestamps**: Removed all hardcoded dates (`2026-10-29`). All case receipts, actions, SLA countdowns, and SQL seeds use live UTC/IST timestamps (`datetime.now(IST)` and PostgreSQL `CURRENT_DATE - INTERVAL '...'`).
- **Live Computed SLA & Alert Engine (`backend/app/services/sla_and_alerts.py`)**: Dynamically evaluates `expected_disposal_date` against category-specific `sla_configs` (`SLA_DEFAULT_DAYS=30`, `SLA_WARNING_DAYS=5`, Ragging=7d, Certificate Withholding=15d) to generate `SLA_BREACH`, `APPROACHING_SLA`, `PENDING_REMINDER`, `ESCALATION`, and `OVERLAP` alerts.

### 2. JWT Authentication & Per-Bureau Role-Based Access Control (`backend/app/core/security.py`)
- **HS256 JWT Authentication**: Passwords are hashed using `PBKDF2-HMAC-SHA256` (100,000 iterations) and tokens are signed with `JWT_SECRET`.
- **Per-Bureau RBAC Enforcement**:
  - `COMPETENT_AUTHORITY` (`admin@aicte-india.org` / `Aicte@2026`): Full cross-bureau read/write, joint coordination, transfer, escalation, and manual case linking.
  - `BUREAU_OFFICER` (`rb.nodal@aicte-india.org`, `pgrc.nodal@aicte-india.org`, `ab.nodal@aicte-india.org`, `vc.nodal@aicte-india.org`): Can directly mutate status on cases owned by their bureau, upload evidence, and initiate inter-bureau transfers; cross-bureau unauthorized status mutations return `HTTP 403 Forbidden`.
  - `REGIONAL_OFFICER` (`sro.chennai@aicte-india.org`): Regional jurisdiction (`Southern`) and field inspection transfer workflows.

### 3. Multi-Factor Entity & Content Overlap Engine (`backend/app/services/overlap_engine.py`)
- **Tuned 78.0% Fuzzy Entity Resolution**: Uses `rapidfuzz` (when installed) and an exact mathematical **Jaro-Winkler + Levenshtein + Stopword-Filtered Distinctive Token-Set Ratio** resolver. Generic higher-education tokens (*"institute", "college", "engineering", "technology"*) and city-only matches are stripped/capped so unrelated colleges in the same city (e.g., *"St. Josephs College of Engineering Chennai"* vs *"Velammal Institute of Technology Chennai"*) are **never** falsely matched.
- **Content-Based & Temporal Overlap**: Computes a weighted **Composite Overlap Score (0–100%)** combining:
  - **Institution Entity Confidence (45%)**
  - **Content & Category Relatedness (35%)**: Word/Stem TF-IDF Cosine Similarity on complaint descriptions + Regulatory Category Relatedness Matrix (e.g., *Fee Refund* ↔ *Unapproved Intake* = 85%).
  - **Temporal Proximity (20%)**: Exponential decay over a 90-day window between complaint receipt dates.
- **Manual Link / Unlink with Immutable Audit Trail**: `POST /api/v1/overlaps/link` and `POST /api/v1/overlaps/unlink` allow officers to link or unlink cases with a mandatory speaking reason, logging entries in both `workflow_history` and `audit_logs`.

### 4. Search, Pagination, File Uploads & Server-Side PDF/CSV Reports
- **Paginated Multi-Parameter Search (`GET /api/v1/cases`)**: Supports `q`, `institution_name`, `pid`, `case_id`, `bureau`, `region`, `category`, `status`, `special_flag`, `date_from`, `date_to`, `page`, and `page_size`.
- **Explicit Transfer & Escalate Endpoints**: `POST /api/v1/cases/{id}/transfer` and `POST /api/v1/cases/{id}/escalate`.
- **Real File Upload (`POST /api/v1/cases/{id}/documents`)**: Multipart upload validating file extension & 10 MB size limit, persisting files to `backend/uploads/` and metadata to `uploaded_documents`.
- **Complete MIS Reports (`backend/app/api/v1/endpoints/reports.py`)**: Includes `/summary`, `/bureau-wise`, `/region-wise`, `/institution-wise`, `/ageing`, `/disposal-rate`, `/monthly-quarterly`, `/export/csv`, and ReportLab-powered server PDF generation (`/export/pdf`).

---

## Data Sources & Known Limitations (Item 15)

### Data Sources
- **Public AICTE Approved Institutions (`backend/data/aicte_approved_institutions_public.csv`)**: Contains 12 real AICTE-approved technical institutions across Southern, South-Western, Western, North-Western, South-Central, Central, and Eastern regions with verified AICTE Permanent IDs (`1-6111471` Velammal Institute of Technology, `1-5104051` Tamilnadu College of Engineering, `1-8141561` Acharya Institute of Technology, `1-2739841` Dr. Ambedkar Institute of Technology, `1-9554891` SVERI's College of Engineering Pandharpur, `1-3204491` R. N. G. Patel Institute of Technology, `1-4274953` Khalsa College of Engineering & Technology, `1-3634045571` Manav Rachna International Institute of Research and Studies, `1-4169394` Panipat Institute of Engineering & Technology, `1-451965541` School of Engineering and Technology SPMVV Tirupati, `1-3799438` Anjaneya University Raipur, and `1-5079002231` Government Engineering College Bhojpur).
- **Bulk CSV Importer**: Additional AICTE public EOA institution dumps can be imported via CLI (`python3 backend/scripts/import_aicte_institutions.py <path.csv>`) or via the `POST /api/v1/institutions/import-csv` endpoint.
- **Simulated Grievance Dockets**: While the institutions and Permanent IDs are real public AICTE records, all complaint cases, officer names, and inspection notes are **synthetic demonstration scenarios** created strictly to model the MIC/AICTE problem statement and do not represent real regulatory findings against those institutions.

### System Limitations & Production Considerations
1. **Entity Disambiguation on Sister Campuses**: Trusts operating multiple AICTE-approved colleges under nearly identical names in the same pin code (e.g., Group Campus I vs. Group Campus II) require either an AICTE Permanent ID (`1-XXXXXXX`), Application Number, or distinct campus alias to disambiguate above the 78% threshold.
2. **Semantic Text Matching Language Scope**: The built-in TF-IDF cosine similarity engine operates on English and transliterated terms. Multilingual regional-language grievances (Hindi, Tamil, Marathi, etc.) require an upstream translation or multilingual embedding layer before TF-IDF comparison.
3. **File Storage Backend**: Supporting documents are stored on local container volume (`backend/uploads/`). For multi-node horizontal scaling, replace local disk writes with an S3-compatible object store (NIC Cloud / MeghRaj).

---

## Quick Start & Verification

### Run Automated Unit & Integration Tests (17 Tests)
```bash
cd backend
PYTHONPATH=. python3 -m unittest discover -v -s tests
```

### Run Full Stack via Docker Compose
```bash
cp .env.example .env
docker compose up --build -d
```
- **Web Application**: `http://localhost:8080`
- **FastAPI Swagger Docs**: `http://localhost:8000/docs`
