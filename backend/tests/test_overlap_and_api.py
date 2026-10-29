import io
import os
import tempfile
import unittest
from fastapi.testclient import TestClient

# Point DATABASE_URL to a clean temporary SQLite file for deterministic testing
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"

from app.main import app
from app.core.database import db
from app.services.overlap_engine import (
    CrossBureauOverlapEngine,
    jaro_winkler_similarity,
    tfidf_cosine_similarity,
    category_similarity,
)

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
        sim_text = tfidf_cosine_similarity(
            "Students denied refund of tuition fee and caution deposit after admission withdrawal",
            "Excess admission fee collected and non-refund of tuition fee to withdrawing CSE students"
        )
        self.assertGreater(sim_text, 35.0)
        cat_rel = category_similarity("Fee Refund & Overcharging", "Unapproved Intake / Excess Admission")
        self.assertEqual(cat_rel, 85.0)

    # ------------------------------------------------------------------
    # 3. JWT Authentication & Per-Bureau RBAC Permissions
    # ------------------------------------------------------------------
    def test_08_jwt_login_and_invalid_credentials(self):
        bad = self.client.post("/api/v1/auth/login", json={"email": "admin@aicte-india.org", "password": "WrongPassword"})
        self.assertEqual(bad.status_code, 401)

        token = self._login("rb.nodal@aicte-india.org", "RbOfficer@2026")
        me = self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["officer"]["bureau_code"], "RB")

    def test_09_rbac_blocks_cross_bureau_unauthorized_status_mutation(self):
        """An RB officer cannot directly mutate a PGRC-owned case status without transfer/coordination."""
        rb_token = self._login("rb.nodal@aicte-india.org", "RbOfficer@2026")
        res = self.client.post(
            "/api/v1/cases/AICTE-PGRC-0812/workflow",
            headers={"Authorization": f"Bearer {rb_token}"},
            json={
                "new_status": "Disposed",
                "remarks": "Attempting unauthorized closure from another bureau",
                "next_action": "None",
                "supporting_doc": "Unauthorized.pdf"
            }
        )
        self.assertEqual(res.status_code, 403)

    def test_10_owning_bureau_and_competent_authority_can_update_workflow(self):
        pgrc_token = self._login("pgrc.nodal@aicte-india.org", "PgrcOfficer@2026")
        res = self.client.post(
            "/api/v1/cases/AICTE-PGRC-0812/workflow",
            headers={"Authorization": f"Bearer {pgrc_token}"},
            json={
                "new_status": "Under Process",
                "remarks": "Verified 12 of 18 student bank account details for NEFT refund.",
                "next_action": "Obtain remaining 6 student NEFT mandates",
                "supporting_doc": "PGRC_Verification_0812.pdf"
            }
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("Verified 12 of 18", res.json()["case"]["lastAction"])

    # ------------------------------------------------------------------
    # 4. Transfer, Escalate & Real File Upload Endpoints
    # ------------------------------------------------------------------
    def test_11_explicit_transfer_and_escalate_actions(self):
        ab_token = self._login("ab.nodal@aicte-india.org", "AbOfficer@2026")
        # Transfer AICTE-AB-0192 from AB to RB
        tr_res = self.client.post(
            "/api/v1/cases/AICTE-AB-0192/transfer",
            headers={"Authorization": f"Bearer {ab_token}"},
            json={
                "to_bureau": "RB",
                "target_officer": "Dr. Ananya Nair",
                "transfer_reason": "Merging EVC lab deficiency docket with ongoing RB Show-Cause proceedings.",
                "next_action": "Include lab terminal shortfall in SHC hearing",
                "transfer_order_doc": "AB_to_RB_Transfer_0192.pdf"
            }
        )
        self.assertEqual(tr_res.status_code, 200)
        self.assertEqual(tr_res.json()["case"]["status"], "Transferred")
        self.assertEqual(tr_res.json()["case"]["bureau"], "RB")

        # Escalate by RB officer
        rb_token = self._login("rb.nodal@aicte-india.org", "RbOfficer@2026")
        esc_res = self.client.post(
            "/api/v1/cases/AICTE-AB-0192/escalate",
            headers={"Authorization": f"Bearer {rb_token}"},
            json={
                "escalation_authority": "Standing Hearing Committee (SHC)",
                "escalation_reason": "Combined intake and lab deficit violation referred to SHC.",
                "next_action": "Conduct SHC hearing",
                "supporting_doc": "SHC_Referral_0192.pdf"
            }
        )
        self.assertEqual(esc_res.status_code, 200)
        self.assertEqual(esc_res.json()["case"]["status"], "Escalated")

    def test_12_supporting_document_upload_and_download(self):
        hq_token = self._login("admin@aicte-india.org", "Aicte@2026")
        pdf_bytes = b"%PDF-1.4 Fake Official Inspection Report Content for Testing"
        up_res = self.client.post(
            "/api/v1/cases/AICTE-RB-0409/documents",
            headers={"Authorization": f"Bearer {hq_token}"},
            files={"file": ("Inspection_Affidavit_0409.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
        )
        self.assertEqual(up_res.status_code, 201, up_res.text)
        doc_id = up_res.json()["doc_id"]

        dl_res = self.client.get(f"/api/v1/cases/AICTE-RB-0409/documents/{doc_id}/download")
        self.assertEqual(dl_res.status_code, 200)
        self.assertEqual(dl_res.content, pdf_bytes)

    # ------------------------------------------------------------------
    # 5. Manual Link / Unlink with Audit Trail
    # ------------------------------------------------------------------
    def test_13_manual_case_link_and_unlink_with_audit_trail(self):
        hq_token = self._login("admin@aicte-india.org", "Aicte@2026")
        lnk_res = self.client.post(
            "/api/v1/overlaps/link",
            headers={"Authorization": f"Bearer {hq_token}"},
            json={
                "source_case_id": "AICTE-PGRC-0795",
                "target_case_id": "AICTE-RB-0441",
                "reason": "Both cases involve withholding of original certificates/service books at TNCE Coimbatore.",
                "lead_bureau": "RB"
            }
        )
        self.assertEqual(lnk_res.status_code, 201, lnk_res.text)

        unlnk_res = self.client.post(
            "/api/v1/overlaps/unlink",
            headers={"Authorization": f"Bearer {hq_token}"},
            json={
                "source_case_id": "AICTE-PGRC-0795",
                "target_case_id": "AICTE-RB-0441",
                "reason": "Testing manual unlink audit trail verification."
            }
        )
        self.assertEqual(unlnk_res.status_code, 200, unlnk_res.text)

        audits = self.client.get("/api/v1/auth/audit-logs", headers={"Authorization": f"Bearer {hq_token}"}).json()["items"]
        event_types = [a["event_type"] for a in audits]
        self.assertIn("MANUAL_CASE_LINK", event_types)
        self.assertIn("MANUAL_CASE_UNLINK", event_types)

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

if __name__ == "__main__":
    unittest.main()
