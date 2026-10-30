# AICTE IICM-SD Production Upgrade — Task Plan
Date: 2026-10-30
Goal: Implement 8 Production Features for AICTE Integrated Institution Complaint Monitoring & Status Dashboard (IICM-SD)

## Requested Features Overview:
1. Lead-Bureau designation and "hold" on conflicting actions
2. Auto-escalation ladder on SLA breach
3. Complaint intake API or webhook (Single-window external intake)
4. Multilingual matching (Hindi, Tamil, Hinglish, Tanglish)
5. Institution risk score engine & ranked leaderboard
6. Email and SMS alerts dispatch service with audit feed
7. Tamper-evident audit log (cryptographic SHA-256 hash chaining)
8. Complainant personal data masking by role (DPDP Act 2023 compliance)

## Execution Phases:

### Phase 1: Planning, Schema Updates & Core Services <!-- id: 1 -->
- [x] Create `task_plan.md`, `findings.md`, and `progress.md` <!-- id: 1.1 -->
- [x] Update SQLite schema & migration files (`001_sqlite_schema.sql`, `001_initial_schema.sql`) to include:
  - `institutions`: `lead_bureau`, `action_hold`, `risk_score`, `risk_tier`
  - `complaint_cases`: `escalation_level`, `escalation_status`, `complainant_phone`, `complainant_email`, `external_ref_id`, `source_portal`, `language`
  - `audit_logs`: `prev_hash`, `entry_hash`
  - `notification_dispatches`: table for SMS & Email dispatches
- [x] Run migration / schema alteration on current SQLite DB safely <!-- id: 1.2 -->

### Phase 2: Backend Architecture & Implementation <!-- id: 2 -->
- [x] Feature 7: Implement SHA-256 hash-chained tamper-evident audit logging in `database.py` and verification endpoint in `reports.py` <!-- id: 2.1 -->
- [x] Feature 8: Implement DPDP Act PII masking (names, emails, phones) in `security.py` and unmask API with audit logging <!-- id: 2.2 -->
- [x] Feature 4: Implement multilingual NLP (Hindi, Tamil, phonetic & keyword cross-lingual normalization) in `overlap_engine.py` <!-- id: 2.3 -->
- [x] Feature 5: Implement Institution Risk Score computation engine (frequency, severity, SLA breaches, multi-bureau recurrence) <!-- id: 2.4 -->
- [x] Feature 1: Implement Lead-Bureau designation and Action Hold enforcement preventing conflicting actions by non-lead bureaus <!-- id: 2.5 -->
- [x] Feature 2: Implement Auto-Escalation Ladder service promoting cases across 4 tiers based on overdue SLA days <!-- id: 2.6 -->
- [x] Feature 3: Implement Single-Window Complaint Intake Webhook API (`POST /api/v1/cases/webhook` & `/intake`) with signature check and auto-overlap detection <!-- id: 2.7 -->
- [x] Feature 6: Implement Email & SMS notification dispatch service with dispatch logs and manual trigger API <!-- id: 2.8 -->

### Phase 3: Comprehensive Test Suite <!-- id: 3 -->
- [x] Expand `backend/tests/test_overlap_and_api.py` with tests for all 8 features <!-- id: 3.1 -->
- [x] Run pytest to verify all existing and new tests pass cleanly (25/25 passing) <!-- id: 3.2 -->

### Phase 4: Frontend UI Redesign Integration <!-- id: 4 -->
- [x] Update `frontend/public/index.html` and standalone dashboard to display:
  - Lead-Bureau coordination status & Action Hold warnings
  - Auto-escalation level chips and ladder progress
  - Institution Risk Scores & Top Risk Institutions card / ranking
  - Webhook intake simulation / test tool
  - Multilingual intake testing (Hindi & Tamil)
  - SMS & Email notification dispatch log drawer
  - Tamper-evident audit chain verification badge
  - DPDP Act PII masking with role-based unmask button
- [x] Verify frontend in browser / API (`http://localhost:8000`) <!-- id: 4.1 -->

### Phase 5: Documentation & Git Push <!-- id: 5 -->
- [x] Commit all changes and push to GitHub repository `https://github.com/gokullaxman/-AICTE-IICM-SD` <!-- id: 5.2 -->
- [x] Present completed feature walkthrough and status to user <!-- id: 5.3 -->
