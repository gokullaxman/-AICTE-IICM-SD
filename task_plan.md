# AICTE IICM-SD — Secure Gemini AI Chatbot Integration Task Plan

## Overview
Implement a lightweight, production-oriented AI chatbot powered by Google's official Gemini API (`google-genai` SDK) into the AICTE Integrated Institution Complaint Monitoring & Status Dashboard (IICM-SD).

## Implementation Rules
1. Work directly in the existing repository.
2. Phase 1 (Floating Chatbot & Gemini Integration) must be complete, tested, and verified before starting Phase 2.
3. Phase 2 (Dashboard-Aware Assistant) connects to existing authorized backend services without compromising security, RBAC, or DPDP Act 2023 PII masking.
4. Maintain documentation integrity and 100% regression test pass rate.

---

## Phases & Tasks

### Step 1: Repository Inspection & Architectural Baseline <!-- id: step1 -->
- [x] Inspect FastAPI application initialization (`backend/app/main.py`)
- [x] Inspect frontend structure, login flow, theme support, entry points (`frontend/public/index.html` vs `aicte_complaint_monitoring_dashboard.html`)
- [x] Inspect JWT authentication, claims, and RBAC (`backend/app/core/security.py`, `auth.py`)
- [x] Inspect complaint API schemas, authorization, and DPDP PII masking (`cases.py`, `security.py`)
- [x] Inspect institutions, alerts, overlaps, reports API contracts
- [x] Inspect database abstraction and query methods (`database.py`)
- [x] Inspect deployment files (`docker-compose.yml`, `deploy/nginx/default.conf`, Dockerfile, dependencies)
- [x] Inspect test suite (`test_overlap_and_api.py`)
- [x] Verify live Gemini SDK and API key availability (`gemini-3.1-flash-lite`)

### Step 2: Phase 1 — Floating Chatbot UI & Secure Backend <!-- id: step2 -->
- [x] Add Gemini configuration to `backend/app/core/config.py`
- [x] Update `.env.example` with Gemini configuration placeholders
- [x] Create Pydantic chat schemas in `backend/app/schemas/payloads.py` (`ChatMessageRequest`, `ChatMessageResponse`, `ChatMessageItem`)
- [x] Create `backend/app/services/gemini_chat_service.py` with official `google-genai` client, rate limiting, and safe error conversion
- [x] Create `backend/app/api/v1/endpoints/chat.py` with `POST /api/v1/chat/message`, JWT authentication, rate limiting, and validation
- [x] Register chat router in `backend/app/main.py`
- [x] Implement responsive floating chatbot widget in `frontend/public/index.html`:
  - Fixed bottom-right launcher button
  - Slide-up chat panel with header, status, minimize/close, clear chat
  - Scrollable conversation area with user/assistant bubbles, loading indicator, retry on error
  - Safe text sanitization (no raw HTML rendering)
  - Dark/light mode theme support
- [x] Synchronize `aicte_complaint_monitoring_dashboard.html`

### Step 3: Phase 1 Automated Tests & Regression Suite <!-- id: step3 -->
- [x] Create `backend/tests/test_chat_phase1.py` with mocked Gemini SDK covering all 15 Phase 1 test cases
- [x] Run test suite: verify new chat tests pass and all 25 existing tests pass cleanly (100%)

### Step 4: Phase 1 End-to-End Verification <!-- id: step4 -->
- [x] Verify live chat request/response flow against live server
- [x] Verify error states, rate limiting, and missing key fallback
- [x] Confirm Phase 1 criteria met before proceeding to Phase 2

### Step 5: Phase 2 — Dashboard-Aware Assistant Context Layer <!-- id: step5 -->
- [x] Design controlled context retrieval layer in `backend/app/services/gemini_chat_service.py` (`DashboardContextRetriever`)
- [x] Connect to existing backend services: cases, institutions, SLA alerts, overlaps, risk engine, audit chain
- [x] Enforce strict RBAC and DPDP Act 2023 complainant PII masking on retrieved evidence
- [x] Implement prompt injection defense (evidence treated as untrusted data, read-only guardrails)

### Step 6: Phase 2 Automated Tests <!-- id: step6 -->
- [x] Create `backend/tests/test_chat_phase2.py` testing authorization, data grounding, PII masking, and prompt injection defense
- [x] Run full test suite (Phase 1 [15] + Phase 2 [15] + Existing 25 tests = 55 tests passed 100%)

### Step 7: Packaging, Deployment & Documentation <!-- id: step7 -->
- [x] Create `backend/requirements.txt`, root `requirements.txt`, and `backend/Dockerfile`
- [x] Verify Docker Compose and Nginx configuration
- [x] Document setup, environment variables, and usage in `README.md`
- [x] Final implementation report and Git push
