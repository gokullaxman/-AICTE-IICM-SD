-- ============================================================================
-- AICTE Integrated Institution Complaint Monitoring & Status Dashboard (IICM-SD)
-- Production Relational Schema (PostgreSQL 16 & SQLite 3 Compatible)
-- Version 3.1.0 with Lead-Bureau Action Hold, Auto-Escalation Ladder,
-- Hash-Chained Audit Logs, Multilingual Intake, and Risk Score Architecture
-- ============================================================================

CREATE TABLE IF NOT EXISTS bureaus (
    code VARCHAR(16) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    short_name VARCHAR(64) NOT NULL,
    jurisdiction VARCHAR(128) NOT NULL,
    nodal_email VARCHAR(128) NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(64) PRIMARY KEY,
    email VARCHAR(128) UNIQUE NOT NULL,
    full_name VARCHAR(128) NOT NULL,
    designation VARCHAR(128) NOT NULL,
    bureau_code VARCHAR(16) REFERENCES bureaus(code),
    role VARCHAR(32) NOT NULL, -- 'COMPETENT_AUTHORITY', 'BUREAU_OFFICER', 'REGIONAL_OFFICER'
    region_scope VARCHAR(64) DEFAULT 'ALL',
    password_hash VARCHAR(255) NOT NULL,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sla_configs (
    category VARCHAR(128) PRIMARY KEY,
    prescribed_days INTEGER NOT NULL,
    warning_threshold_days INTEGER NOT NULL DEFAULT 5,
    pending_reminder_days INTEGER NOT NULL DEFAULT 7,
    escalation_authority VARCHAR(128) NOT NULL
);

CREATE TABLE IF NOT EXISTS institutions (
    pid VARCHAR(32) PRIMARY KEY,
    application_no VARCHAR(64),
    name VARCHAR(255) NOT NULL,
    aliases TEXT NOT NULL, -- pipe-separated aliases for universal SQL compatibility
    address TEXT NOT NULL,
    city VARCHAR(100) NOT NULL,
    district VARCHAR(100),
    state VARCHAR(100) NOT NULL,
    pincode VARCHAR(16),
    region VARCHAR(64) NOT NULL,
    institution_type VARCHAR(100),
    affiliating_university VARCHAR(255),
    approval_status VARCHAR(128) NOT NULL,
    sanctioned_intake INTEGER DEFAULT 0,
    established_year INTEGER,
    principal_name VARCHAR(128),
    contact_email VARCHAR(128),
    contact_phone VARCHAR(64),
    coordination_status VARCHAR(32) DEFAULT 'SINGLE', -- 'SINGLE', 'UNRESOLVED', 'COORDINATED'
    coordination_note TEXT,
    lead_bureau VARCHAR(16) REFERENCES bureaus(code),
    action_hold INTEGER DEFAULT 0,
    risk_score REAL DEFAULT 0.0,
    risk_tier VARCHAR(16) DEFAULT 'LOW'
);

CREATE TABLE IF NOT EXISTS complaint_cases (
    case_id VARCHAR(64) PRIMARY KEY,
    pid VARCHAR(32) NOT NULL REFERENCES institutions(pid) ON DELETE CASCADE,
    inst_variant_logged VARCHAR(255) NOT NULL,
    receipt_date VARCHAR(32) NOT NULL,
    category VARCHAR(128) NOT NULL,
    complainant VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    status VARCHAR(32) NOT NULL, -- 'Pending', 'Under Process', 'Transferred', 'Escalated', 'Disposed'
    bureau VARCHAR(16) NOT NULL REFERENCES bureaus(code),
    assigned_officer VARCHAR(128) NOT NULL,
    last_action TEXT,
    next_action TEXT,
    last_action_date VARCHAR(32),
    expected_disposal_date VARCHAR(32) NOT NULL,
    match_method VARCHAR(255),
    match_confidence REAL DEFAULT 100.0,
    escalation_level INTEGER DEFAULT 0,
    escalation_status VARCHAR(32) DEFAULT 'NORMAL',
    last_escalated_at VARCHAR(32),
    complainant_phone VARCHAR(64),
    complainant_email VARCHAR(128),
    external_ref_id VARCHAR(64),
    source_portal VARCHAR(64) DEFAULT 'MANUAL',
    language VARCHAR(16) DEFAULT 'en',
    action_hold INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS case_links (
    link_id VARCHAR(64) PRIMARY KEY,
    source_case_id VARCHAR(64) NOT NULL REFERENCES complaint_cases(case_id) ON DELETE CASCADE,
    target_case_id VARCHAR(64) NOT NULL REFERENCES complaint_cases(case_id) ON DELETE CASCADE,
    pid VARCHAR(32) NOT NULL REFERENCES institutions(pid) ON DELETE CASCADE,
    link_type VARCHAR(32) NOT NULL, -- 'AUTO_PID', 'AI_FUZZY_CONTENT', 'MANUAL_OFFICER', 'JOINT_DIRECTIVE'
    similarity_score REAL DEFAULT 100.0,
    lead_bureau VARCHAR(16),
    coordination_directive TEXT,
    linked_by VARCHAR(128) NOT NULL,
    linked_at VARCHAR(32) NOT NULL,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS workflow_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id VARCHAR(64) NOT NULL REFERENCES complaint_cases(case_id) ON DELETE CASCADE,
    action_timestamp VARCHAR(32) NOT NULL,
    action_type VARCHAR(64) NOT NULL, -- 'INTAKE', 'STATUS_UPDATE', 'TRANSFER', 'ESCALATE', 'AUTO_ESCALATION', 'JOINT_COORDINATION', 'MANUAL_LINK', 'MANUAL_UNLINK', 'DOC_UPLOAD'
    from_bureau VARCHAR(16),
    to_bureau VARCHAR(16) NOT NULL,
    officer VARCHAR(128) NOT NULL,
    action_remarks TEXT NOT NULL,
    supporting_doc VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS uploaded_documents (
    doc_id VARCHAR(64) PRIMARY KEY,
    case_id VARCHAR(64) NOT NULL REFERENCES complaint_cases(case_id) ON DELETE CASCADE,
    original_filename VARCHAR(255) NOT NULL,
    stored_filename VARCHAR(255) NOT NULL,
    mime_type VARCHAR(128) NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    uploaded_by VARCHAR(128) NOT NULL,
    uploaded_at VARCHAR(32) NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id VARCHAR(64) PRIMARY KEY,
    alert_type VARCHAR(32) NOT NULL, -- 'OVERLAP', 'SLA_BREACH', 'APPROACHING_SLA', 'PENDING_REMINDER', 'ESCALATION'
    severity VARCHAR(16) NOT NULL, -- 'CRITICAL', 'HIGH', 'MEDIUM'
    pid VARCHAR(32) REFERENCES institutions(pid) ON DELETE CASCADE,
    case_id VARCHAR(64) REFERENCES complaint_cases(case_id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    created_at VARCHAR(32) NOT NULL,
    acknowledged INTEGER DEFAULT 0,
    acknowledged_by VARCHAR(128),
    acknowledged_at VARCHAR(32)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp VARCHAR(32) NOT NULL,
    actor_email VARCHAR(128) NOT NULL,
    actor_role VARCHAR(64) NOT NULL,
    actor_bureau VARCHAR(16) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    target_entity VARCHAR(128) NOT NULL,
    details TEXT NOT NULL,
    prev_hash VARCHAR(64),
    entry_hash VARCHAR(64)
);

CREATE TABLE IF NOT EXISTS notification_dispatches (
    dispatch_id VARCHAR(64) PRIMARY KEY,
    alert_id VARCHAR(64),
    case_id VARCHAR(64),
    channel VARCHAR(16) NOT NULL, -- 'EMAIL', 'SMS'
    recipient VARCHAR(128) NOT NULL,
    destination VARCHAR(128) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    message_body TEXT NOT NULL,
    dispatched_at VARCHAR(32) NOT NULL,
    delivery_status VARCHAR(32) NOT NULL -- 'DELIVERED', 'QUEUED', 'SIMULATED_SENT'
);
