import csv
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from app.core.config import settings
from app.core.security import hash_password

IST = timezone(timedelta(hours=5, minutes=30))

def now_ist() -> datetime:
    return datetime.now(IST)

def today_str(offset_days: int = 0) -> str:
    return (now_ist() + timedelta(days=offset_days)).strftime("%Y-%m-%d")

def timestamp_str(offset_days: int = 0, hour: int = 11, minute: int = 30) -> str:
    dt = (now_ist() + timedelta(days=offset_days)).replace(hour=hour, minute=minute, second=0)
    return dt.strftime("%Y-%m-%d %H:%M")

class RelationalDatabase:
    """
    Production Relational Database Adapter supporting both PostgreSQL (via psycopg2)
    and SQLite3 using a unified cursor interface. Reads DATABASE_URL from settings.
    """
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or settings.DATABASE_URL
        self.is_postgres = self.db_url.startswith("postgresql://") or self.db_url.startswith("postgres://")
        if not self.is_postgres:
            # Extract sqlite file path
            if self.db_url.startswith("sqlite:///"):
                self.sqlite_path = self.db_url.replace("sqlite:///", "", 1)
            else:
                self.sqlite_path = self.db_url
            os.makedirs(os.path.dirname(os.path.abspath(self.sqlite_path)), exist_ok=True)

    @contextmanager
    def connection(self):
        if self.is_postgres:
            import psycopg2
            import psycopg2.extras
            conn = psycopg2.connect(self.db_url)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        else:
            conn = sqlite3.connect(self.sqlite_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _adapt_query(self, sql: str) -> str:
        if self.is_postgres:
            return sql.replace("?", "%s")
        return sql

    def fetch_all(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        q = self._adapt_query(sql)
        with self.connection() as conn:
            if self.is_postgres:
                import psycopg2.extras
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(q, params)
                    return [dict(r) for r in cur.fetchall()]
            else:
                cur = conn.execute(q, params)
                return [dict(r) for r in cur.fetchall()]

    def fetch_one(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Tuple[Any, ...] = ()) -> int:
        q = self._adapt_query(sql)
        with self.connection() as conn:
            if self.is_postgres:
                with conn.cursor() as cur:
                    cur.execute(q, params)
                    return cur.rowcount
            else:
                cur = conn.execute(q, params)
                return cur.rowcount

    def initialize_and_seed(self) -> None:
        migrations_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../db/migrations"))
        schema_file = "001_initial_schema.sql" if self.is_postgres else "001_sqlite_schema.sql"
        schema_path = os.path.join(migrations_dir, schema_file)
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_sql = f.read()

        with self.connection() as conn:
            if self.is_postgres:
                with conn.cursor() as cur:
                    cur.execute(schema_sql)
            else:
                conn.executescript(schema_sql)

        # Check if already seeded
        existing = self.fetch_one("SELECT COUNT(*) AS cnt FROM bureaus")
        if existing and existing.get("cnt", 0) > 0:
            return

        self._seed_reference_data()

    def _seed_reference_data(self) -> None:
        # 1. Seed Bureaus
        bureaus = [
            ("RB", "Regulation Bureau (RB)", "Regulation Bureau", "National Regulatory Enforcement & Show-Cause", "rb.nodal@aicte-india.org"),
            ("PGRC", "Public Grievance Redressal Cell (PGRC)", "PGRC Cell", "Student, Parent & Stakeholder Grievance Redressal", "pgrc.nodal@aicte-india.org"),
            ("AB", "Approval Bureau (AB)", "Approval Bureau", "Extension of Approval (EoA), Intake & EVC Scrutiny", "ab.nodal@aicte-india.org"),
            ("VC", "Vigilance & Monitoring Cell (VC)", "Vigilance Cell", "Forensic Payroll, PAN Deduplication & Whistleblower Inquiry", "vc.nodal@aicte-india.org"),
            ("RO", "Regional Offices Directorate (RO)", "Regional Office", "SWRO / SRO / NRO / WRO / ERO / CRO Field Inspections", "sro.chennai@aicte-india.org"),
            ("HQ", "Competent Authority / AICTE Administration", "AICTE HQ", "All-India Oversight & Standing Hearing Committee", "admin@aicte-india.org")
        ]
        for b in bureaus:
            self.execute(
                "INSERT INTO bureaus (code, name, short_name, jurisdiction, nodal_email) VALUES (?, ?, ?, ?, ?)",
                b
            )

        # 2. Seed Users / Officers with real PBKDF2 password hashes
        users = [
            ("USR-HQ-01", "admin@aicte-india.org", "Prof. T. G. Sitharam / Member Secretary Desk", "Competent Authority & Chairman Desk", "HQ", "COMPETENT_AUTHORITY", "ALL", hash_password("Aicte@2026")),
            ("USR-RB-01", "rb.nodal@aicte-india.org", "Dr. Ananya Nair", "Deputy Director (Regulation Bureau)", "RB", "BUREAU_OFFICER", "ALL", hash_password("RbOfficer@2026")),
            ("USR-PGRC-01", "pgrc.nodal@aicte-india.org", "Shri V. K. Sharma", "Assistant Director (PGRC)", "PGRC", "BUREAU_OFFICER", "ALL", hash_password("PgrcOfficer@2026")),
            ("USR-AB-01", "ab.nodal@aicte-india.org", "Shri M. S. Gill", "Under Secretary (Approval Bureau)", "AB", "BUREAU_OFFICER", "ALL", hash_password("AbOfficer@2026")),
            ("USR-VC-01", "vc.nodal@aicte-india.org", "Shri D. P. Singh", "Chief Vigilance Officer (VC)", "VC", "BUREAU_OFFICER", "ALL", hash_password("VcOfficer@2026")),
            ("USR-RO-01", "sro.chennai@aicte-india.org", "Dr. M. Sundaresan", "Regional Officer (SRO Chennai)", "RO", "REGIONAL_OFFICER", "Southern", hash_password("RoOfficer@2026"))
        ]
        for u in users:
            self.execute(
                "INSERT INTO users (user_id, email, full_name, designation, bureau_code, role, region_scope, password_hash, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                u
            )

        # 3. Seed SLA Configs
        sla_rules = [
            ("Fee Refund & Overcharging", 30, 5, 7, "Director (PGRC) & Standing Hearing Committee"),
            ("Unapproved Intake / Excess Admission", 30, 5, 7, "Advisor-I (Regulation Bureau)"),
            ("Original Certificate Withholding", 15, 4, 5, "Director (PGRC) & Regional Officer"),
            ("Faculty Salary & Qualification Norms", 30, 5, 7, "Deputy Director (Regulation Bureau)"),
            ("Infrastructure & Lab Deficit", 30, 5, 7, "Advisor (Approval Bureau)"),
            ("Fake Faculty / Biometric Attendance", 30, 5, 7, "Chief Vigilance Officer & RB"),
            ("Hostel & Campus Safety / Ragging", 7, 2, 2, "National Anti-Ragging Monitoring Cell & PGRC")
        ]
        for s in sla_rules:
            self.execute(
                "INSERT INTO sla_configs (category, prescribed_days, warning_threshold_days, pending_reminder_days, escalation_authority) VALUES (?, ?, ?, ?, ?)",
                s
            )

        # 4. Import Real AICTE Approved Institutions from CSV
        csv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/aicte_approved_institutions_public.csv"))
        self.import_institutions_from_csv(csv_path)

        # Update initial coordination notes on the 4 multi-bureau overlap institutions
        overlap_updates = [
            ("UNRESOLVED", "Potential Overlapping Cases – Coordination Required between PGRC, RB & AB", "1-6111471"),
            ("UNRESOLVED", "Potential Overlapping Cases – Coordination Required between PGRC & RB", "1-5104051"),
            ("UNRESOLVED", "Potential Overlapping Cases – Coordination Required between RB, PGRC & VC", "1-3634045571"),
            ("UNRESOLVED", "Potential Overlapping Cases – Coordination Required between PGRC & RO", "1-8141561"),
            ("COORDINATED", "Joint RB + PGRC Coordination Directive Executed", "1-9554891"),
        ]
        for st, note, pid in overlap_updates:
            self.execute("UPDATE institutions SET coordination_status = ?, coordination_note = ? WHERE pid = ?", (st, note, pid))

        # 5. Seed Dynamic Relative-Timestamp Complaint Cases (No hardcoded static calendar dates!)
        cases = [
            # OVERLAP GROUP 1: Velammal Institute of Technology (1-6111471) -> PGRC + RB + AB
            (
                "AICTE-PGRC-0812", "1-6111471", "Velammal Institute of Technology, Panchetti Chennai",
                today_str(-45), "Fee Refund & Overcharging", "Batch of 18 B.E. (CSE & AI-DS) Students",
                "Non-refund of tuition and caution deposit (Rs. 1.25L per student) after admission withdrawal within AICTE cutoff window; excess admission fee demanded.",
                "Under Process", "PGRC", "Shri V. K. Sharma",
                f"{today_str(-11)}: Final show-cause reminder issued to Principal for immediate NEFT fee refund to 18 students.",
                "Verify bank UTR refund receipts or refer to Regulation Bureau for coercive penalty.",
                today_str(-11), today_str(-15), # 15 days overdue (SLA Breached)
                "Exact PID 1-6111471 + TF-IDF Content Cluster (99.4%)", 99.4
            ),
            (
                "AICTE-RB-0409", "1-6111471", "VIT Panchetti (Velammal Tech Chennai)",
                today_str(-22), "Unapproved Intake / Excess Admission", "Directorate of Technical Education (DoTE) & Vigilance Ref",
                "Admission of 36 students in excess of AICTE sanctioned intake (678 seats) in CSE & AI-DS and retention of tuition fee for unapproved seats.",
                "Under Process", "RB", "Dr. Ananya Nair",
                f"{today_str(-4)}: Action initiated under AICTE Approval Process Handbook Clause 8; Show-Cause Notice issued.",
                "Convene Standing Hearing Committee (SHC) and merge PGRC student fee refund affidavits.",
                today_str(-4), today_str(8),
                "Fuzzy Alias ('VIT Panchetti' -> 1-6111471) + Content Overlap (91.8%)", 91.8
            ),
            (
                "AICTE-AB-0192", "1-6111471", "Velammal Engg Ponneri Campus",
                today_str(-14), "Infrastructure & Lab Deficit", "Expert Visit Committee (EVC) Observation",
                "Shortfall in AI & Data Science high-performance computing terminals relative to increased student intake during EoA scrutiny.",
                "Pending", "AB", "Shri M. S. Gill",
                f"{today_str(-9)}: EVC deficiency note logged; flagged by Cross-Bureau Overlap Engine.",
                "Hold EoA intake expansion pending disposal of RB-0409 and PGRC-0812.",
                today_str(-9), today_str(16),
                "Pincode Anchor (601204) + Jaro-Winkler Name Match (86.4%)", 86.4
            ),

            # OVERLAP GROUP 2: Tamilnadu College of Engineering, Coimbatore (1-5104051) -> PGRC + RB
            (
                "AICTE-PGRC-0795", "1-5104051", "Tamilnadu College of Engineering, Karumathampatti Coimbatore",
                today_str(-28), "Original Certificate Withholding", "Ms. Divya Krishnan & 9 B.E. Graduates",
                "Institution withholding original 10th, 12th, and B.E. degree mark sheets demanding unauthorized clearance fees, blocking GATE fellowship joining.",
                "Escalated", "PGRC", "Shri V. K. Sharma",
                f"{today_str(-6)}: Escalated to Director (PGRC) due to non-return of original certificates despite 48-hour directive.",
                "Coordinate with Regulation Bureau inspection team to enforce immediate release of certificates.",
                today_str(-6), today_str(-13), # SLA for Certificate Withholding is 15d -> 13d overdue!
                "Exact PID 1-5104051 Verified (100.0%)", 100.0
            ),
            (
                "AICTE-RB-0441", "1-5104051", "TNCE Coimbatore (Karumathampatti)",
                today_str(-26), "Faculty Salary & Qualification Norms", "TNCE Faculty Welfare Representation",
                "Non-disbursement of 6 months 7th CPC salary arrears and coercive withholding of original faculty service books and experience certificates.",
                "Under Process", "RB", "Dr. Ananya Nair",
                f"{today_str(-3)}: Surprise inspection committee constituted by Regulation Bureau.",
                "Include PGRC-0795 student certificate retention docket in RB Inspection Committee mandate.",
                today_str(-3), today_str(4), # Approaching SLA (4 days left)
                "Acronym & Location Match ('TNCE Coimbatore' -> 1-5104051) (89.5%)", 89.5
            ),

            # OVERLAP GROUP 3: Manav Rachna International Institute of Research and Studies (1-3634045571) -> RB + PGRC + VC
            (
                "AICTE-RB-0388", "1-3634045571", "Manav Rachna International Institute of Research and Studies, Faridabad",
                today_str(-38), "Fake Faculty / Biometric Attendance", "AICTE National PAN Deduplication Audit",
                "Automated PAN & Aadhaar biometric audit flagged 11 adjunct faculty records overlapping with full-time appointments at other NCR institutions.",
                "Under Process", "RB", "Dr. Ananya Nair",
                f"{today_str(-8)}: Standing Hearing Committee examined Registrar; payroll verification requested.",
                "Reconcile Vigilance Cell forensic payroll findings before issuing final speaking order.",
                today_str(-8), today_str(-8), # 8 days overdue
                "Exact PID 1-3634045571 Verified (100.0%)", 100.0
            ),
            (
                "AICTE-VC-0104", "1-3634045571", "MRIIRS Faridabad (Sector 43 Aravalli Hills)",
                today_str(-27), "Fake Faculty / Biometric Attendance", "Ministry of Education Innovation Cell Vigilance Reference",
                "Whistleblower representation alleging discrepancy in contracted faculty attendance logs and bank salary scroll disbursements in Engineering departments.",
                "Under Process", "VC", "Shri D. P. Singh",
                f"{today_str(-5)}: Bank scroll and PF challan forensic verification completed; linked with RB-0388.",
                "Submit joint vigilance report to Regulation Bureau.",
                today_str(-5), today_str(3), # Approaching SLA (3 days left)
                "Alias ('MRIIRS Faridabad') + High Content Similarity (93.2%)", 93.2
            ),
            (
                "AICTE-PGRC-0860", "1-3634045571", "Manav Rachna Deemed University, Faridabad",
                today_str(-12), "Infrastructure & Lab Deficit", "3rd Year B.Tech Mechanical Students",
                "Students reported shortage of dedicated lab instructors and non-availability of faculty mentors during scheduled practical sessions.",
                "Pending", "PGRC", "Shri V. K. Sharma",
                f"{today_str(-10)}: Preliminary scrutiny completed; overlap detected with RB-0388 and VC-0104.",
                "Map to RB-0388 & VC-0104 joint faculty investigation.",
                today_str(-10), today_str(18),
                "Fuzzy Name & Pincode 121004 Match (87.6%)", 87.6
            ),

            # OVERLAP GROUP 4: Acharya Institute of Technology, Bengaluru (1-8141561) -> PGRC + RO
            (
                "AICTE-PGRC-0891", "1-8141561", "Acharya Institute of Technology, Soladevanahalli Bengaluru",
                today_str(-5), "Hostel & Campus Safety / Ragging", "Parent of 1st Year B.E. Student",
                "Urgent complaint regarding hostel night-warden absence, delayed anti-ragging squad response, and arbitrary hostel mess fine levied on freshers.",
                "Under Process", "PGRC", "Shri V. K. Sharma",
                f"{today_str(-2)}: Anti-Ragging Committee CCTV and Proctorial report sought within 48 hours.",
                "Coordinate with Regional Office (RO) field inspection team visiting Bengaluru campus.",
                today_str(-2), today_str(2), # 7-day SLA for Ragging -> 2 days remaining!
                "Exact PID 1-8141561 Verified (100.0%)", 100.0
            ),
            (
                "AICTE-RO-0215", "1-8141561", "AIT Bengaluru (Hesaraghatta Main Road)",
                today_str(-16), "Fee Refund & Overcharging", "Regional Office Grievance Redressal Camp",
                "Representation from 14 hostel residents regarding unapproved compulsory skill-development and hostel maintenance fee levy.",
                "Transferred", "RO", "Dr. M. Sundaresan",
                f"{today_str(-3)}: Regional Office campus verification conducted; docket transferred for PGRC/RB joint action.",
                "Combine Regional Office campus inspection report with PGRC-0891.",
                today_str(-3), today_str(14),
                "Alias ('AIT Bengaluru') + Pincode 560107 Match (88.9%)", 88.9
            ),

            # GROUP 5 (COORDINATED OVERLAP): SVERI's College of Engineering, Pandharpur (1-9554891) -> PGRC + RB
            (
                "AICTE-PGRC-0740", "1-9554891", "SVERI's College of Engineering, Pandharpur",
                today_str(-40), "Faculty Salary & Qualification Norms", "12 Assistant Professors (ECE & Mechanical)",
                "Grievance regarding delayed Dearness Allowance (DA) and Provident Fund remittance for academic session.",
                "Under Process", "PGRC", "Shri V. K. Sharma",
                f"{today_str(-7)}: Joint Coordination Order executed with RB-0395; duplicate inquiry closed.",
                "Monitor disbursement of arrears under RB compliance schedule.",
                today_str(-7), today_str(10),
                "Coordinated via Joint Directive (PID 1-9554891)", 99.0
            ),
            (
                "AICTE-RB-0395", "1-9554891", "SVERI Pandharpur (Gopalpur Solapur)",
                today_str(-35), "Faculty Salary & Qualification Norms", "Western Regional Office Compliance Audit",
                "Compliance audit flagged arrears in faculty pay scale implementation and cadence of cadre ratio.",
                "Under Process", "RB", "Dr. Ananya Nair",
                f"{today_str(-7)}: Designated Lead Bureau; combined hearing held for PGRC & RB dockets.",
                "Verify compliance affidavit due in 10 days.",
                today_str(-7), today_str(10),
                "Coordinated via Joint Directive (PID 1-9554891)", 96.4
            ),

            # SINGLE BUREAU CASES ACROSS OTHER REAL AICTE INSTITUTIONS
            (
                "AICTE-PGRC-0902", "1-4169394", "Panipat Institute of Engineering & Technology, Samalkha",
                today_str(-22), "Original Certificate Withholding", "Shri Rohit Chauhan (B.Tech Student)",
                "Student requested return of original migration certificate after medical withdrawal; registrar response overdue.",
                "Pending", "PGRC", "Shri V. K. Sharma",
                f"{today_str(-12)}: Notice served to Registrar; response overdue beyond 15-day SLA.",
                "Issue SLA breach warning and summon Principal online.",
                today_str(-12), today_str(-7), # 7 days overdue
                "Exact PID 1-4169394 Verified (100.0%)", 100.0
            ),
            (
                "AICTE-AB-0155", "1-2739841", "Dr. Ambedkar Institute of Technology, Bengaluru",
                today_str(-55), "Infrastructure & Lab Deficit", "Annual EoA Scrutiny Committee",
                "Barrier-free accessibility ramp and digital library subscription renewal verification.",
                "Disposed", "AB", "Shri M. S. Gill",
                f"{today_str(-28)}: Compliance verified via geo-tagged photographs; case disposed within SLA.",
                "Closed – No further action required.",
                today_str(-28), today_str(-25),
                "Exact PID 1-2739841 Verified (100.0%)", 100.0
            ),
            (
                "AICTE-PGRC-0688", "1-3204491", "R. N. G. Patel Institute of Technology, Bardoli Surat",
                today_str(-48), "Fee Refund & Overcharging", "Ms. Prachi Desai (B.E. Admission Cancellation)",
                "Refund of 1st-semester tuition fee upon admission cancellation before final counselling round.",
                "Disposed", "PGRC", "Shri V. K. Sharma",
                f"{today_str(-21)}: Full fee of Rs. 84,000 refunded via RTGS; verified by student.",
                "Closed – Disposed within prescribed timeline.",
                today_str(-21), today_str(-18),
                "Exact PID 1-3204491 Verified (100.0%)", 100.0
            ),
            (
                "AICTE-RB-0310", "1-4274953", "Khalsa College of Engineering & Technology, Amritsar",
                today_str(-62), "Unapproved Intake / Excess Admission", "Admission Supervisory Committee Reference",
                "Reconciliation of Tuition Fee Waiver (TFW) supernumerary quota seats against sanctioned strength.",
                "Disposed", "RB", "Dr. Ananya Nair",
                f"{today_str(-34)}: IKGPTU counselling seat matrix reconciled; case disposed.",
                "Closed – Disposed within prescribed timeline.",
                today_str(-34), today_str(-32),
                "Exact PID 1-4274953 Verified (100.0%)", 100.0
            )
        ]

        for c in cases:
            self.execute(
                """INSERT INTO complaint_cases (
                    case_id, pid, inst_variant_logged, receipt_date, category, complainant,
                    description, status, bureau, assigned_officer, last_action, next_action,
                    last_action_date, expected_disposal_date, match_method, match_confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                c
            )

        # 6. Seed Case Links
        links = [
            ("LNK-001", "AICTE-PGRC-0812", "AICTE-RB-0409", "1-6111471", "AI_FUZZY_CONTENT", 91.8, "RB", "Auto-linked via PID 1-6111471 & Intake/Fee Refund semantic correlation", "AI Overlap Engine", timestamp_str(-20), 1),
            ("LNK-002", "AICTE-RB-0388", "AICTE-VC-0104", "1-3634045571", "AI_FUZZY_CONTENT", 93.2, "RB", "Linked duplicate faculty PAN audit and payroll whistleblower inquiry", "Shri D. P. Singh", timestamp_str(-5), 1),
            ("LNK-003", "AICTE-PGRC-0740", "AICTE-RB-0395", "1-9554891", "JOINT_DIRECTIVE", 97.5, "RB", "Joint Coordination Directive executed; PGRC inquiry merged into RB compliance schedule", "Dr. Ananya Nair", timestamp_str(-7), 1)
        ]
        for lnk in links:
            self.execute(
                """INSERT INTO case_links (
                    link_id, source_case_id, target_case_id, pid, link_type, similarity_score,
                    lead_bureau, coordination_directive, linked_by, linked_at, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                lnk
            )

        # 7. Seed Chronological Workflow History
        histories = [
            ("AICTE-PGRC-0812", timestamp_str(-45, 10, 15), "INTAKE", None, "PGRC", "Grievance Portal Auto-Ingest", "Batch petition by 18 B.E. students registered alleging non-refund of Rs. 1.25L tuition fee after withdrawal.", "Student_Batch18_Petitions.pdf"),
            ("AICTE-PGRC-0812", timestamp_str(-32, 14, 20), "STATUS_UPDATE", "PGRC", "PGRC", "Shri V. K. Sharma", "15-day parawise comments notice issued to Principal, Velammal Institute of Technology.", "PGRC_Notice_0812.pdf"),
            ("AICTE-PGRC-0812", timestamp_str(-11, 16, 45), "STATUS_UPDATE", "PGRC", "PGRC", "Shri V. K. Sharma", "Final show-cause reminder issued; flagged for cross-bureau coordination with RB-0409.", "PGRC_FinalReminder_0812.pdf"),

            ("AICTE-RB-0409", timestamp_str(-22, 11, 5), "INTAKE", None, "RB", "Dr. Ananya Nair", "DoTE reference received regarding admission of 36 students beyond sanctioned intake.", "DoTE_Reference_0409.pdf"),
            ("AICTE-RB-0409", timestamp_str(-4, 15, 30), "STATUS_UPDATE", "RB", "RB", "Dr. Ananya Nair", "Show-Cause Notice issued under APH Clause 8.1; auto-linked with PGRC-0812.", "RB_SCN_0409.pdf"),

            ("AICTE-AB-0192", timestamp_str(-14, 9, 45), "INTAKE", None, "AB", "Shri M. S. Gill", "EVC scrutiny noted computing terminal shortfall in AI-DS laboratory.", "EVC_Report_0192.pdf"),

            ("AICTE-PGRC-0795", timestamp_str(-28, 13, 10), "INTAKE", None, "PGRC", "Shri V. K. Sharma", "Grievance filed by 10 graduates regarding withholding of original degree certificates.", "Graduates_Affidavit_0795.pdf"),
            ("AICTE-PGRC-0795", timestamp_str(-6, 17, 0), "ESCALATE", "PGRC", "PGRC", "Shri V. K. Sharma", "Escalated to Director (PGRC) & Regional Officer due to non-compliance with 48-hour release directive.", "Escalation_Order_0795.pdf"),

            ("AICTE-RB-0441", timestamp_str(-26, 10, 0), "INTAKE", None, "RB", "Dr. Ananya Nair", "Faculty association complaint on 6-month 7th CPC pay arrears and service book withholding.", "Faculty_Rep_0441.pdf"),
            ("AICTE-RB-0441", timestamp_str(-3, 14, 50), "STATUS_UPDATE", "RB", "RB", "Dr. Ananya Nair", "Surprise Inspection Committee notified by Regulation Bureau.", "RB_Committee_0441.pdf"),

            ("AICTE-RB-0388", timestamp_str(-38, 9, 30), "INTAKE", None, "RB", "Dr. Ananya Nair", "National PAN deduplication audit flagged 11 duplicate faculty records.", "PAN_Audit_0388.pdf"),
            ("AICTE-RB-0388", timestamp_str(-8, 16, 0), "STATUS_UPDATE", "RB", "RB", "Dr. Ananya Nair", "Standing Hearing Committee hearing completed; awaiting VC forensic bank scroll report.", "SHC_Minutes_0388.pdf"),

            ("AICTE-VC-0104", timestamp_str(-27, 15, 0), "INTAKE", None, "VC", "Shri D. P. Singh", "MoE Innovation Cell forwarded whistleblower dossier on faculty attendance & salary scrolls.", "MoE_Vigilance_0104.pdf"),
            ("AICTE-VC-0104", timestamp_str(-5, 11, 40), "MANUAL_LINK", "VC", "VC", "Shri D. P. Singh", "Forensic bank scroll verification linked with RB-0388.", "VC_Forensic_Link_0104.pdf"),

            ("AICTE-PGRC-0860", timestamp_str(-12, 12, 20), "INTAKE", None, "PGRC", "Shri V. K. Sharma", "Students reported shortage of permanent lab instructors in Mechanical Engineering.", "Student_Petition_0860.pdf"),

            ("AICTE-PGRC-0891", timestamp_str(-5, 8, 50), "INTAKE", None, "PGRC", "Shri V. K. Sharma", "Anti-ragging & hostel safety grievance registered; 48-hour Proctorial report sought.", "AntiRagging_0891.pdf"),

            ("AICTE-RO-0215", timestamp_str(-16, 11, 30), "INTAKE", None, "RO", "Dr. M. Sundaresan", "Regional Office grievance camp petition on unauthorized hostel maintenance fee.", "RO_Camp_0215.pdf"),
            ("AICTE-RO-0215", timestamp_str(-3, 17, 15), "TRANSFER", "RO", "PGRC", "Dr. M. Sundaresan", "Campus inspection completed; docket transferred to HQ for joint coordination with PGRC-0891.", "RO_Transfer_Memo_0215.pdf"),

            ("AICTE-PGRC-0740", timestamp_str(-40, 10, 15), "INTAKE", None, "PGRC", "Shri V. K. Sharma", "Faculty grievance on DA & PF contribution delay registered.", "SVERI_Faculty_0740.pdf"),
            ("AICTE-PGRC-0740", timestamp_str(-7, 15, 0), "JOINT_COORDINATION", "PGRC", "RB", "Dr. Ananya Nair", "Joint Coordination Directive executed with RB-0395; RB designated Lead Bureau.", "Joint_Directive_1-9554891.pdf"),

            ("AICTE-RB-0395", timestamp_str(-35, 14, 30), "INTAKE", None, "RB", "Dr. Ananya Nair", "WRO compliance audit referred faculty cadre ratio & pay arrears.", "WRO_Audit_0395.pdf"),
            ("AICTE-RB-0395", timestamp_str(-7, 15, 0), "JOINT_COORDINATION", "RB", "RB", "Dr. Ananya Nair", "Combined hearing held for RB-0395 and PGRC-0740; single compliance schedule issued.", "Joint_Directive_1-9554891.pdf"),

            ("AICTE-PGRC-0902", timestamp_str(-22, 11, 10), "INTAKE", None, "PGRC", "Shri V. K. Sharma", "Original migration certificate withholding complaint logged.", "Petition_0902.pdf"),
            ("AICTE-AB-0155", timestamp_str(-28, 15, 20), "STATUS_UPDATE", "AB", "AB", "Shri M. S. Gill", "Compliance verified via geo-tagged photographs; case disposed.", "Disposal_0155.pdf"),
            ("AICTE-PGRC-0688", timestamp_str(-21, 14, 10), "STATUS_UPDATE", "PGRC", "PGRC", "Shri V. K. Sharma", "RTGS fee refund UTR confirmed by student; case disposed.", "UTR_Receipt_0688.pdf"),
            ("AICTE-RB-0310", timestamp_str(-34, 16, 45), "STATUS_UPDATE", "RB", "RB", "Dr. Ananya Nair", "TFW supernumerary quota seats reconciled with university; case disposed.", "Closure_0310.pdf")
        ]
        for h in histories:
            self.execute(
                """INSERT INTO workflow_history (
                    case_id, action_timestamp, action_type, from_bureau, to_bureau,
                    officer, action_remarks, supporting_doc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                h
            )

        # 8. Seed Initial Audit Log
        self.log_audit(
            actor_email="admin@aicte-india.org",
            actor_role="COMPETENT_AUTHORITY",
            actor_bureau="HQ",
            event_type="SYSTEM_BOOTSTRAP",
            target_entity="DATABASE",
            details="Initialized AICTE IICM-SD relational schema, imported 12 public AICTE-approved institutions, and seeded multi-bureau cases."
        )

    def import_institutions_from_csv(self, csv_filepath: str) -> int:
        if not os.path.exists(csv_filepath):
            return 0
        imported = 0
        with open(csv_filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing = self.fetch_one("SELECT pid FROM institutions WHERE pid = ?", (row["pid"].strip(),))
                if existing:
                    continue
                self.execute(
                    """INSERT INTO institutions (
                        pid, application_no, name, aliases, address, city, district, state,
                        pincode, region, institution_type, affiliating_university,
                        approval_status, sanctioned_intake, established_year,
                        principal_name, contact_email, contact_phone,
                        coordination_status, coordination_note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'SINGLE', 'Single Bureau / Clean')""",
                    (
                        row["pid"].strip(),
                        row.get("application_no", "").strip(),
                        row["name"].strip(),
                        row.get("aliases", "").strip(),
                        row["address"].strip(),
                        row["city"].strip(),
                        row.get("district", "").strip(),
                        row["state"].strip(),
                        row.get("pincode", "").strip(),
                        row["region"].strip(),
                        row.get("institution_type", "Private Self-Financing").strip(),
                        row.get("affiliating_university", "").strip(),
                        row.get("approval_status", "EOA Granted 2026-27").strip(),
                        int(row.get("sanctioned_intake", 0) or 0),
                        int(row.get("established_year", 2005) or 2005),
                        row.get("principal_name", "").strip(),
                        row.get("contact_email", "").strip(),
                        row.get("contact_phone", "").strip()
                    )
                )
                imported += 1
        return imported

    def log_audit(self, actor_email: str, actor_role: str, actor_bureau: str, event_type: str, target_entity: str, details: str) -> None:
        self.execute(
            """INSERT INTO audit_logs (timestamp, actor_email, actor_role, actor_bureau, event_type, target_entity, details)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (timestamp_str(0, now_ist().hour, now_ist().minute), actor_email, actor_role, actor_bureau, event_type, target_entity, details)
        )

db = RelationalDatabase()
