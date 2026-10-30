import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Point DATABASE_URL to a clean temporary SQLite file for deterministic testing
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"

from fastapi.testclient import TestClient
from app.main import app
from app.core.database import db
from app.core.config import settings
from app.services.overlap_engine import (
    CrossBureauOverlapEngine,
    jaro_winkler_similarity,
    tfidf_cosine_similarity,
    category_similarity,
    cross_lingual_semantic_expansion,
    detect_language
)
from app.services.risk_engine import calculate_institution_risk, get_institution_risk_leaderboard

class TestAICTEProductionSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.initialize_and_seed()
        cls.client = TestClient(app)
        cls.engine = CrossBureauOverlapEngine(fuzzy_threshold=78.0)
        cls.institutions = db.fetch_all("SELECT * FROM institutions")

    def _login(self, email: str, password: str) -> str:
        res = self.client.post("/api/v1/auth/login", json={"email": email, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertIn("access_token", data)
        return data["access_token"]

    # ------------------------------------------------------------------
    # 1. Fuzzy Entity Resolution & False-Positive Rejection Tests
    # ------------------------------------------------------------------
    def test_01_exact_pid_match(self):
        res = self.engine.resolve_institution_entity(
            raw_name="Unrecognised Variant Name",
            raw_pid="1-6111471",
            institutions=self.institutions
        )
        self.assertTrue(res["matched"])
        self.assertEqual(res["institution"]["pid"], "1-6111471")
        self.assertEqual(res["confidence"], 100.0)

    def test_02_embedded_pid_in_text_match(self):
        res = self.engine.resolve_institution_entity(
            raw_name="Complaint against college PID 1-5104051 in Coimbatore",
            raw_pid=None,
            institutions=self.institutions
        )
        self.assertTrue(res["matched"])
        self.assertEqual(res["institution"]["pid"], "1-5104051")
        self.assertGreaterEqual(res["confidence"], 99.0)

    def test_03_acronym_and_alias_resolution(self):
        res = self.engine.resolve_institution_entity(
            raw_name="VIT Panchetti Chennai",
            raw_pid=None,
            institutions=self.institutions
        )
        self.assertTrue(res["matched"])
        self.assertEqual(res["institution"]["pid"], "1-6111471")
        self.assertGreaterEqual(res["confidence"], 78.0)

    def test_04_typo_tolerant_jaro_winkler_resolution(self):
        res = self.engine.resolve_institution_entity(
            raw_name="Velamal Institute of Technolgy Pancheti Chennai",
            raw_pid=None,
            institutions=self.institutions
        )
        self.assertTrue(res["matched"])
        self.assertEqual(res["institution"]["pid"], "1-6111471")
        self.assertGreaterEqual(res["confidence"], 78.0)

    def test_05_false_positive_rejection_on_generic_words_and_same_city(self):
        """
        Ensures an unrelated college in Chennai ('St. Josephs College of Engineering Chennai')
        is REJECTED (< 78% threshold) and does NOT falsely match Velammal Institute of Technology, Chennai.
        """
        res = self.engine.resolve_institution_entity(
            raw_name="St Josephs College of Engineering and Technology Chennai",
            raw_pid=None,
            institutions=self.institutions
        )
        self.assertFalse(res["matched"], f"Expected rejection but got match: {res}")
        self.assertLess(res["confidence"], 78.0)

    def test_06_empty_or_missing_input_rejected(self):
        res = self.engine.resolve_institution_entity(raw_name="   ", raw_pid="", institutions=self.institutions)
        self.assertFalse(res["matched"])
        self.assertEqual(res["confidence"], 0.0)

    # ------------------------------------------------------------------
    # 2. Content-Based TF-IDF, Category Relatedness & Temporal Overlap
    # ------------------------------------------------------------------
    def test_07_content_tfidf_and_category_relatedness(self):
        # Correlated pair: Fee refund refusal vs unapproved intake
        sim = category_similarity("Fee Refund & Overcharging", "Unapproved Intake / Excess Admission")
        self.assertEqual(sim, 85.0)

        # TF-IDF similarity between overlapping complaints
        text_a = "Students demanded refund of excess tuition fee Rs. 1.25L after admission cancellation."
        text_b = "Complaint alleging college admitted excess students and withheld fee refund deposit."
        tfidf_sim = tfidf_cosine_similarity(text_a, text_b)
        self.assertGreaterEqual(tfidf_sim, 50.0)

    # ------------------------------------------------------------------
    # 3. RBAC, JWT Auth & Cross-Bureau Mutation Safeguards
    # ------------------------------------------------------------------
    def test_08_jwt_login_and_invalid_credentials(self):
        token = self._login("rb.nodal@aicte-india.org", "RbOfficer@2026")
        self.assertTrue(len(token) > 20)

        # Invalid credentials rejection
        bad_res = self.client.post("/api/v1/auth/login", json={"email": "rb.nodal@aicte-india.org", "password": "WrongPassword"})
        self.assertEqual(bad_res.status_code, 401)

    def test_09_rbac_blocks_cross_bureau_unauthorized_status_mutation(self):
        # PGRC Officer should NOT be permitted to modify RB-owned case AICTE-RB-0409 without transfer or coordination
        pgrc_token = self._login("pgrc.nodal@aicte-india.org", "PgrcOfficer@2026")
        res = self.client.post(
            "/api/v1/cases/AICTE-RB-0409/workflow",
            headers={"Authorization": f"Bearer {pgrc_token}"},
            json={"new_status": "Disposed", "remarks": "Unauthorized unilateral closure attempt", "next_action": "None"}
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("RBAC Permission Denied", res.json()["detail"])

    def test_10_owning_bureau_and_competent_authority_can_update_workflow(self):
        rb_token = self._login("rb.nodal@aicte-india.org", "RbOfficer@2026")
        res = self.client.post(
            "/api/v1/cases/AICTE-RB-0409/workflow",
            headers={"Authorization": f"Bearer {rb_token}"},
            json={
                "new_status": "Under Process",
                "remarks": "Standing Hearing Committee examined evidence dossier.",
                "next_action": "Issue formal compliance schedule."
            }
        )
        self.assertEqual(res.status_code, 200)

        # Competent Authority (HQ) has cross-bureau mutation rights
        hq_token = self._login("admin@aicte-india.org", "Aicte@2026")
        res_hq = self.client.post(
            "/api/v1/cases/AICTE-RB-0409/workflow",
            headers={"Authorization": f"Bearer {hq_token}"},
            json={
                "new_status": "Under Process",
                "remarks": "Chairman Desk reviewed progress; joint disposal mandated.",
                "next_action": "Proceed with SHC order."
            }
        )
        self.assertEqual(res_hq.status_code, 200)

    # ------------------------------------------------------------------
    # 4. Explicit Workflow Progression: Transfer & Escalate Actions
    # ------------------------------------------------------------------
    def test_11_explicit_transfer_and_escalate_actions(self):
        rb_token = self._login("rb.nodal@aicte-india.org", "RbOfficer@2026")
        # Escalate
        esc_res = self.client.post(
            "/api/v1/cases/AICTE-RB-0409/escalate",
            headers={"Authorization": f"Bearer {rb_token}"},
            json={
                "escalation_authority": "Standing Hearing Committee (SHC)",
                "escalation_reason": "Defiance of show-cause notice regarding unapproved seats.",
                "next_action": "Summon Chairman & Registrar for in-person hearing."
            }
        )
        self.assertEqual(esc_res.status_code, 200)
        self.assertEqual(esc_res.json()["case"]["status"], "Escalated")

    # ------------------------------------------------------------------
    # 5. Supporting Document Upload, Download & Manual Case Linking
    # ------------------------------------------------------------------
    def test_12_supporting_document_upload_and_download(self):
        rb_token = self._login("rb.nodal@aicte-india.org", "RbOfficer@2026")
        dummy_file = io.BytesIO(b"%PDF-1.4 Dummy AICTE Official Directive Content")
        upload_res = self.client.post(
            "/api/v1/cases/AICTE-RB-0409/documents",
            headers={"Authorization": f"Bearer {rb_token}"},
            files={"file": ("Official_Inspection_Order.pdf", dummy_file, "application/pdf")}
        )
        self.assertEqual(upload_res.status_code, 201)
        doc_data = upload_res.json()
        self.assertIn("doc_id", doc_data)

        # Download document
        dl_res = self.client.get(f"/api/v1/cases/AICTE-RB-0409/documents/{doc_data['doc_id']}/download")
        self.assertEqual(dl_res.status_code, 200)
        self.assertEqual(dl_res.content, b"%PDF-1.4 Dummy AICTE Official Directive Content")

    def test_13_manual_case_link_and_unlink_with_audit_trail(self):
        rb_token = self._login("rb.nodal@aicte-india.org", "RbOfficer@2026")
        link_res = self.client.post(
            "/api/v1/overlaps/link",
            headers={"Authorization": f"Bearer {rb_token}"},
            json={
                "source_case_id": "AICTE-RB-0409",
                "target_case_id": "AICTE-AB-0192",
                "reason": "Correlating computing lab deficiency with excess student admissions.",
                "lead_bureau": "RB"
            }
        )
        self.assertEqual(link_res.status_code, 201)

        # Unlink
        unlink_res = self.client.post(
            "/api/v1/overlaps/unlink",
            headers={"Authorization": f"Bearer {rb_token}"},
            json={
                "source_case_id": "AICTE-RB-0409",
                "target_case_id": "AICTE-AB-0192",
                "reason": "De-linked following separate adjudication."
            }
        )
        self.assertEqual(unlink_res.status_code, 200)

    # ------------------------------------------------------------------
    # 6. Dynamic SLA Alerts, Search Pagination, Profile & MIS Reports
    # ------------------------------------------------------------------
    def test_14_live_computed_sla_and_overlap_alerts(self):
        res = self.client.get("/api/v1/alerts/")
        self.assertEqual(res.status_code, 200)
        items = res.json()["items"]
        types = {a["type"] for a in items}
        self.assertIn("OVERLAP", types)
        self.assertIn("SLA_BREACH", types)
        self.assertIn("APPROACHING_SLA", types)

    def test_15_case_search_date_range_and_pagination(self):
        res = self.client.get("/api/v1/cases/?page=1&page_size=5&region=Southern")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["page_size"], 5)
        self.assertGreaterEqual(data["total"], 5)
        self.assertLessEqual(len(data["items"]), 5)

    def test_16_institution_360_profile_endpoint(self):
        res = self.client.get("/api/v1/institutions/1-6111471/profile")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["institution"]["pid"], "1-6111471")
        self.assertTrue(data["has_overlap"])
        self.assertGreaterEqual(len(data["consolidated_case_history"]), 3)

    def test_17_full_reports_and_csv_pdf_exports(self):
        for ep in ["/summary", "/bureau-wise", "/region-wise", "/institution-wise", "/ageing", "/disposal-rate", "/monthly-quarterly"]:
            r = self.client.get(f"/api/v1/reports{ep}")
            self.assertEqual(r.status_code, 200, f"Failed on {ep}")

        csv_r = self.client.get("/api/v1/reports/export/csv")
        self.assertEqual(csv_r.status_code, 200)
        self.assertIn("text/csv", csv_r.headers["content-type"])
        self.assertIn("AICTE-PGRC-0812", csv_r.text)

        pdf_r = self.client.get("/api/v1/reports/export/pdf")
        self.assertEqual(pdf_r.status_code, 200)
        self.assertEqual(pdf_r.headers["content-type"], "application/pdf")
        self.assertTrue(pdf_r.content.startswith(b"%PDF"))

    # ==================================================================
    # NEW TESTS: FEATURES 1 THROUGH 8
    # ==================================================================

    # Feature 1: Lead-Bureau Designation & Action Hold Enforcement
    def test_18_lead_bureau_and_action_hold_blocks_unilateral_mutation(self):
        hq_token = self._login("admin@aicte-india.org", "Aicte@2026")
        pgrc_token = self._login("pgrc.nodal@aicte-india.org", "PgrcOfficer@2026")
        rb_token = self._login("rb.nodal@aicte-india.org", "RbOfficer@2026")

        # 1. Designate RB as Lead Bureau with action_hold=True on institution 1-6111471
        coord_res = self.client.post(
            "/api/v1/overlaps/1-6111471/lead-bureau",
            headers={"Authorization": f"Bearer {hq_token}"},
            json={
                "lead_bureau": "RB",
                "action_hold": True,
                "coordination_directive": "RB designated lead for unapproved intake and student refunds."
            }
        )
        self.assertEqual(coord_res.status_code, 200)
        self.assertTrue(coord_res.json()["action_hold"])

        # 2. Non-lead bureau (PGRC) attempts unilateral status update on AICTE-PGRC-0812 -> BLOCKED with 409
        blocked_res = self.client.post(
            "/api/v1/cases/AICTE-PGRC-0812/workflow",
            headers={"Authorization": f"Bearer {pgrc_token}"},
            json={
                "new_status": "Disposed",
                "remarks": "Unilateral fee refund closure attempt while under RB coordination hold.",
                "next_action": "None"
            }
        )
        self.assertEqual(blocked_res.status_code, 409)
        self.assertIn("ACTION HOLD ACTIVE", blocked_res.json()["detail"])

        # 3. Lead bureau (RB) or Competent Authority (HQ) CAN perform coordinated workflow
        allowed_res = self.client.post(
            "/api/v1/cases/AICTE-RB-0409/workflow",
            headers={"Authorization": f"Bearer {rb_token}"},
            json={
                "new_status": "Under Process",
                "remarks": "Lead Bureau held consolidated hearing covering both PGRC and RB dockets.",
                "next_action": "Submit compliance report."
            }
        )
        self.assertEqual(allowed_res.status_code, 200)

        # 4. Release action hold
        rel_res = self.client.post(
            "/api/v1/overlaps/1-6111471/release-hold",
            headers={"Authorization": f"Bearer {hq_token}"}
        )
        self.assertEqual(rel_res.status_code, 200)
        self.assertFalse(rel_res.json()["action_hold"])

    # Feature 2: Auto-Escalation Ladder on SLA Breach
    def test_19_auto_escalation_ladder_triggers_promotion(self):
        hq_token = self._login("admin@aicte-india.org", "Aicte@2026")

        # Explicitly reset an overdue case to test ladder trigger
        db.execute(
            "UPDATE complaint_cases SET escalation_level = 0, escalation_status = 'NORMAL', status = 'Under Process' WHERE case_id = 'AICTE-PGRC-0812'"
        )

        res = self.client.post(
            "/api/v1/cases/auto-escalate",
            headers={"Authorization": f"Bearer {hq_token}"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "COMPLETED")
        self.assertGreaterEqual(data["cases_escalated_count"], 1)

        # Verify that breached cases were escalated in DB
        breached_case = self.client.get("/api/v1/cases/AICTE-PGRC-0812").json()["case"]
        self.assertEqual(breached_case["status"], "Escalated")
        self.assertGreaterEqual(breached_case["escalationLevel"], 2)

    # Feature 3: Complaint Intake API & Webhook
    def test_20_single_window_webhook_intake_api(self):
        # 1. Webhook with valid X-Webhook-Secret
        webhook_payload = {
            "external_reference_id": "CPGRAMS/2026/TN/99412",
            "source_portal": "CPGRAMS",
            "institution_name": "Velammal Institute of Technology",
            "aicte_pid": "1-6111471",
            "category": "Fee Refund & Overcharging",
            "complainant_name": "Anil S. Deshmukh",
            "complainant_email": "anil.deshmukh@gmail.com",
            "complainant_phone": "+91 9823456789",
            "description": "Student withdrew admission within AICTE date window; college refused caution deposit refund.",
            "language": "en"
        }
        res = self.client.post(
            "/api/v1/cases/webhook",
            headers={"X-Webhook-Secret": settings.WEBHOOK_SECRET},
            json=webhook_payload
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["status"], "PROCESSED")
        self.assertEqual(data["external_reference_id"], "CPGRAMS/2026/TN/99412")
        self.assertTrue(data["case_id"].startswith("AICTE-"))
        self.assertTrue(data["has_overlap"])

        # 2. Reject unauthorized webhook request
        unauth_res = self.client.post(
            "/api/v1/cases/webhook",
            headers={"X-Webhook-Secret": "invalid_secret_key"},
            json=webhook_payload
        )
        self.assertEqual(unauth_res.status_code, 401)

    # Feature 4: Multilingual Matching (Hindi & Tamil)
    def test_21_multilingual_matching_hindi_and_tamil(self):
        # 1. Test language detection
        self.assertEqual(detect_language("कॉलेज छात्रों के मूल प्रमाण पत्र वापस नहीं दे रहा है"), "hi")
        self.assertEqual(detect_language("கல்லூரி நிர்வாகம் மாணவர்களின் அசல் சான்றிதழ்களை தராமல் உள்ளது"), "ta")
        self.assertEqual(detect_language("College withholding original certificates"), "en")

        # 2. Test cross-lingual concept expansion
        hi_text = "कॉलेज छात्रों के मूल प्रमाण पत्र और अंकतालिका वापस नहीं दे रहा है"
        exp_hi, lang_hi, cat_hi = cross_lingual_semantic_expansion(hi_text)
        self.assertEqual(lang_hi, "hi")
        self.assertEqual(cat_hi, "Original Certificate Withholding")
        self.assertIn("certificate_withholding", exp_hi)

        ta_text = "கல்லூரி நிர்வாகம் மாணவர்களின் அசல் சான்றிதழ்களை தராமல் கூடுதல் கட்டணம் கேட்கிறது"
        exp_ta, lang_ta, cat_ta = cross_lingual_semantic_expansion(ta_text)
        self.assertEqual(lang_ta, "ta")
        self.assertIn("certificate_withholding", exp_ta)
        self.assertIn("fee_refund", exp_ta)

        # 3. Test cross-lingual TF-IDF similarity against English case
        en_text = "Institution is withholding original degree certificates and demanding unauthorized fees."
        sim_hi = tfidf_cosine_similarity(en_text, hi_text)
        sim_ta = tfidf_cosine_similarity(en_text, ta_text)
        self.assertGreaterEqual(sim_hi, 45.0)
        self.assertGreaterEqual(sim_ta, 45.0)

    # Feature 5: Institution Risk Score & Ranked Leaderboard
    def test_22_institution_risk_score_and_leaderboard(self):
        res = self.client.get("/api/v1/institutions/risk-scores?limit=10")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertGreater(data["count"], 0)
        leaderboard = data["leaderboard"]
        self.assertGreater(len(leaderboard), 0)

        # Top institution should be an active multi-bureau overlapping institution (Velammal or Manav Rachna)
        top_inst = leaderboard[0]
        self.assertIn(top_inst["risk_tier"], ("CRITICAL", "HIGH"))
        self.assertGreaterEqual(top_inst["risk_score"], 60.0)
        self.assertGreater(len(top_inst["risk_factors"]), 0)

    # Feature 6: Email and SMS Alerts Dispatch Log
    def test_23_notification_dispatches_email_and_sms(self):
        hq_token = self._login("admin@aicte-india.org", "Aicte@2026")

        # Broadcast critical alerts
        b_res = self.client.post(
            "/api/v1/alerts/broadcast-all",
            headers={"Authorization": f"Bearer {hq_token}"}
        )
        self.assertEqual(b_res.status_code, 200)
        self.assertGreater(b_res.json()["dispatches_generated"], 0)

        # Verify dispatch log feed
        disp_res = self.client.get("/api/v1/alerts/dispatches?limit=20")
        self.assertEqual(disp_res.status_code, 200)
        dispatches = disp_res.json()["items"]
        channels = {d["channel"] for d in dispatches}
        self.assertIn("EMAIL", channels)
        self.assertIn("SMS", channels)

    # Feature 7: Tamper-Evident Hash-Chained Audit Log Verification
    def test_24_tamper_evident_audit_log_cryptographic_verification(self):
        # 1. Verify audit log chain is initially valid
        verify_res = self.client.get("/api/v1/reports/audit-verify")
        self.assertEqual(verify_res.status_code, 200)
        data = verify_res.json()
        self.assertTrue(data["verified"])
        self.assertEqual(data["status"], "TAMPER_EVIDENT_CHAIN_VERIFIED")
        self.assertTrue(len(data["chain_head_hash"]) == 64)

        # 2. Simulate database record tampering
        first_row = db.fetch_one("SELECT audit_id, details FROM audit_logs ORDER BY audit_id ASC LIMIT 1")
        orig_id = first_row["audit_id"]
        orig_details = first_row["details"]

        try:
            # Malicious direct SQL alteration bypassing hash chain
            db.execute("UPDATE audit_logs SET details = 'TAMPERED_DETAILS_MALICIOUS_INJECTION' WHERE audit_id = ?", (orig_id,))

            # Verification MUST fail and catch the tampered record!
            tampered_res = self.client.get("/api/v1/reports/audit-verify")
            self.assertEqual(tampered_res.status_code, 200)
            tamper_data = tampered_res.json()
            self.assertFalse(tamper_data["verified"])
            self.assertEqual(tamper_data["tampered_audit_id"], orig_id)
        finally:
            # Restore pristine details
            db.execute("UPDATE audit_logs SET details = ? WHERE audit_id = ?", (orig_details, orig_id))

        # Re-verify restored chain
        restored_res = self.client.get("/api/v1/reports/audit-verify")
        self.assertTrue(restored_res.json()["verified"])

    # Feature 8: Complainant PII Masking & Lawful Unmask Audit
    def test_25_dpdp_act_pii_masking_and_unmask_audit(self):
        # 1. Anonymous / Public query: PII is masked per DPDP Act 2023
        public_res = self.client.get("/api/v1/cases/AICTE-RB-0441")
        self.assertEqual(public_res.status_code, 200)
        c_pub = public_res.json()["case"]
        self.assertTrue(c_pub["is_pii_masked"])
        self.assertIn("*", c_pub["complainant"])

        # 2. RB Officer viewing their own bureau's case: PII is unmasked
        rb_token = self._login("rb.nodal@aicte-india.org", "RbOfficer@2026")
        rb_res = self.client.get(
            "/api/v1/cases/AICTE-RB-0441",
            headers={"Authorization": f"Bearer {rb_token}"}
        )
        self.assertEqual(rb_res.status_code, 200)
        self.assertFalse(rb_res.json()["case"]["is_pii_masked"])

        # 3. Officer lawfully unmasks PII via dedicated unmask API
        unmask_res = self.client.post(
            "/api/v1/cases/AICTE-RB-0441/unmask-pii",
            headers={"Authorization": f"Bearer {rb_token}"},
            json={
                "justification_reason": "Standing Hearing Committee formal inquiry order under DPDP Section 8.",
                "statutory_reference": "SHC-2026-INQ-0441"
            }
        )
        self.assertEqual(unmask_res.status_code, 200)
        unmask_data = unmask_res.json()
        self.assertTrue(unmask_data["is_pii_unmasked"])
        self.assertEqual(unmask_data["complainant"], "TNCE Faculty Welfare Representation")

        # 4. Verify PII access event is logged in hash-chained audit log
        audit = db.fetch_one("SELECT * FROM audit_logs WHERE event_type = 'PII_UNMASK_ACCESSED' ORDER BY audit_id DESC LIMIT 1")
        self.assertIsNotNone(audit)
        self.assertEqual(audit["target_entity"], "AICTE-RB-0441")
        self.assertIn("SHC-2026-INQ-0441", audit["details"])

if __name__ == "__main__":
    unittest.main()
