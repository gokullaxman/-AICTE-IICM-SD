import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.database import db
from app.core.security import create_jwt_token, decode_jwt_token
from app.services.gemini_chat_service import gemini_chat_service


class TestChatAuthScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.initialize_and_seed()
        cls.client = TestClient(app)

    def setUp(self):
        gemini_chat_service._rate_limits.clear()

    # Scenario 1: A logged-in user sends a chatbot message successfully
    @patch.object(gemini_chat_service, "_get_client")
    def test_01_logged_in_user_sends_chat_message_successfully(self, mock_get_client):
        # 1. Login to obtain a valid HS256 JWT
        login_res = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@aicte-india.org", "password": "Aicte@2026"}
        )
        self.assertEqual(login_res.status_code, 200)
        token = login_res.json()["access_token"]
        self.assertEqual(len(token.split(".")), 3)

        # 2. Mock Gemini model response
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Hello Officer! The IICM Assistant is ready to help."
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        with patch.object(settings, "GEMINI_API_KEY", "test-key-auth-ok"):
            res = self.client.post(
                "/api/v1/chat/message",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": "What is the status of AICTE-RB-0409?"}
            )
            self.assertEqual(res.status_code, 200, res.text)
            data = res.json()
            self.assertIn("reply", data)
            self.assertEqual(data["category"], "CASE_INQUIRY")

    # Scenario 2: A missing token produces a clear authentication error (401)
    def test_02_missing_token_produces_clear_auth_error(self):
        res = self.client.post(
            "/api/v1/chat/message",
            json={"message": "Hello without token"}
        )
        self.assertEqual(res.status_code, 401)
        self.assertIn("Missing Authorization header", res.json()["detail"])
        self.assertEqual(res.headers.get("WWW-Authenticate"), "Bearer")

    # Scenario 3: Malformed tokens are rejected safely with 401
    def test_03_malformed_token_rejected_safely(self):
        malformed_headers = [
            "Bearer ",
            "Basic dXNlcjpwYXNz",
            "Bearer undefined",
            "Bearer null",
            "Bearer [object Object]",
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aicte_hq_session",
            "Bearer Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.sig",
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.invalidsignature123",
            "Bearer a.b",
            "Bearer ...",
        ]

        for auth_val in malformed_headers:
            with self.subTest(auth_val=auth_val):
                res = self.client.post(
                    "/api/v1/chat/message",
                    headers={"Authorization": auth_val},
                    json={"message": "Testing token validation"}
                )
                self.assertEqual(res.status_code, 401, f"Expected 401 for '{auth_val}', got {res.status_code}")
                self.assertIn("bearer", res.headers.get("www-authenticate", "").lower())
                self.assertTrue(len(res.json().get("detail", "")) > 0)

    # Scenario 4: An expired token is rejected (401)
    def test_04_expired_token_rejected(self):
        expired_token = create_jwt_token(
            {"sub": "USR-HQ-01", "email": "admin@aicte-india.org", "role": "COMPETENT_AUTHORITY", "bureau_code": "HQ"},
            expires_hours=-2
        )
        res = self.client.post(
            "/api/v1/chat/message",
            headers={"Authorization": f"Bearer {expired_token}"},
            json={"message": "Testing expired token"}
        )
        self.assertEqual(res.status_code, 401)
        self.assertIn("jwt token has expired", res.json()["detail"].lower())

    # Scenario 5: A user with insufficient permissions cannot access restricted dashboard data (403)
    def test_05_insufficient_permissions_blocked_with_403(self):
        # Login as PGRC Officer
        login_res = self.client.post(
            "/api/v1/auth/login",
            json={"email": "pgrc.nodal@aicte-india.org", "password": "PgrcOfficer@2026"}
        )
        self.assertEqual(login_res.status_code, 200)
        pgrc_token = login_res.json()["access_token"]
        pgrc_headers = {"Authorization": f"Bearer {pgrc_token}"}

        # Attempt to mutate a case owned by RB (AICTE-RB-0409) -> Must return 403 Forbidden
        mutate_res = self.client.post(
            "/api/v1/cases/AICTE-RB-0409/workflow",
            headers=pgrc_headers,
            json={
                "new_status": "Disposed",
                "remarks": "Unauthorized cross-bureau mutation test remarks",
                "next_action": "None"
            }
        )
        self.assertEqual(mutate_res.status_code, 403)
        self.assertIn("rbac permission denied", mutate_res.json()["detail"].lower())

        # Also verify in chatbot context: PGRC officer querying case with individual PII receives masked data
        context_res = gemini_chat_service.context_retriever.retrieve_case_context(
            "AICTE-PGRC-0902",
            {"user_id": "USR-RB-01", "role": "BUREAU_OFFICER", "bureau_code": "RB"}
        )
        self.assertTrue(context_res["found"])
        self.assertIn("DPDP 2023 MASKED", context_res["text"])
        self.assertIn("*", context_res["text"])

    # Scenario 6: The chatbot does not retry indefinitely after a 401 response
    def test_06_no_indefinite_retry_after_401(self):
        # A 401 response is terminal: verify backend returns 401 immediately without loop
        res = self.client.post(
            "/api/v1/chat/message",
            headers={"Authorization": "Bearer bad.token.format"},
            json={"message": "Single 401 check"}
        )
        self.assertEqual(res.status_code, 401)
        # Verify sliding rate limit is not consumed by 401 (rate limit remains clean)
        self.assertEqual(len(gemini_chat_service._rate_limits), 0)

    # Scenario 7: A user can authenticate again and retry successfully
    @patch.object(gemini_chat_service, "_get_client")
    def test_07_authenticate_again_and_retry_successfully(self, mock_get_client):
        # 1. Initial attempt with expired token fails with 401
        expired = create_jwt_token({"sub": "USR-RB-01"}, expires_hours=-1)
        res_fail = self.client.post(
            "/api/v1/chat/message",
            headers={"Authorization": f"Bearer {expired}"},
            json={"message": "Query with expired session"}
        )
        self.assertEqual(res_fail.status_code, 401)

        # 2. Re-authenticate via official login endpoint
        login_res = self.client.post(
            "/api/v1/auth/login",
            json={"email": "rb.nodal@aicte-india.org", "password": "RbOfficer@2026"}
        )
        self.assertEqual(login_res.status_code, 200)
        fresh_token = login_res.json()["access_token"]

        # 3. Retry with fresh token succeeds with 200
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Session successfully restored! How can I assist you, Dr. Nair?"
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        with patch.object(settings, "GEMINI_API_KEY", "test-key-reauth"):
            res_retry = self.client.post(
                "/api/v1/chat/message",
                headers={"Authorization": f"Bearer {fresh_token}"},
                json={"message": "Query after re-authenticating"}
            )
            self.assertEqual(res_retry.status_code, 200)
            self.assertIn("restored", res_retry.json()["reply"])

    # Scenario 8: Existing login and dashboard API requests still work
    def test_08_existing_login_and_dashboard_api_requests_work(self):
        # 1. Login
        login_res = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@aicte-india.org", "password": "Aicte@2026"}
        )
        self.assertEqual(login_res.status_code, 200)
        token = login_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. GET /api/v1/auth/me
        me_res = self.client.get("/api/v1/auth/me", headers=headers)
        self.assertEqual(me_res.status_code, 200)
        self.assertEqual(me_res.json()["officer"]["sub"], "USR-HQ-01")

        # 3. GET /api/v1/cases/
        cases_res = self.client.get("/api/v1/cases/", headers=headers)
        self.assertEqual(cases_res.status_code, 200)
        self.assertIn("items", cases_res.json())

        # 4. GET /api/v1/institutions/
        inst_res = self.client.get("/api/v1/institutions/", headers=headers)
        self.assertEqual(inst_res.status_code, 200)
        self.assertIn("items", inst_res.json())

        # 5. GET /api/v1/alerts/
        alerts_res = self.client.get("/api/v1/alerts/", headers=headers)
        self.assertEqual(alerts_res.status_code, 200)

        # 6. GET /api/v1/reports/summary
        rpt_res = self.client.get("/api/v1/reports/summary", headers=headers)
        self.assertEqual(rpt_res.status_code, 200)
        self.assertIn("total_cases", rpt_res.json())


if __name__ == "__main__":
    unittest.main()
