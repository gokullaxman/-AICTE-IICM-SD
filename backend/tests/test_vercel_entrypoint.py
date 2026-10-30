import os
import sys
import pytest
from fastapi.testclient import TestClient

# Ensure root and backend are in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


class TestVercelEntrypoint:
    """
    Automated verification suite testing Vercel Serverless Function entrypoint (api/index.py),
    serverless database initialization, CORS regex evaluation for *.vercel.app, and static fallback.
    """

    @pytest.fixture(autouse=True)
    def setup_client(self):
        os.environ["VERCEL"] = "1"
        from api.index import app
        self.client = TestClient(app)

    def test_01_vercel_entrypoint_imports_and_is_fastapi(self):
        from api.index import app
        from fastapi import FastAPI
        assert isinstance(app, FastAPI)
        assert app.title.startswith("AICTE")

    def test_02_health_endpoint_returns_200_and_database_loaded(self):
        res = self.client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert data["service"] == "AICTE-IICM-SD-API"
        assert data["institutions_loaded"] > 0
        assert data["cases_loaded"] > 0

    def test_03_root_serves_html_dashboard(self):
        res = self.client.get("/")
        assert res.status_code == 200
        assert "<!DOCTYPE html>" in res.text or "<html" in res.text
        assert "AICTE" in res.text

    def test_04_favicon_returns_204(self):
        res = self.client.get("/favicon.ico")
        assert res.status_code == 204

    def test_05_openapi_docs_work(self):
        res = self.client.get("/docs")
        assert res.status_code == 200

    def test_06_vercel_cors_origin_regex_allows_preview_and_prod_urls(self):
        vercel_origins = [
            "https://aicte-iicm-sd.vercel.app",
            "https://aicte-iicm-sd-git-main-dept.vercel.app",
            "https://aicte-preview-123.vercel.app",
            "http://localhost:3000",
            "http://localhost:8000"
        ]
        for origin in vercel_origins:
            headers = {
                "Origin": origin,
                "Access-Control-Request-Method": "POST"
            }
            res = self.client.options("/api/v1/auth/login", headers=headers)
            assert res.status_code in (200, 204)
            assert res.headers.get("access-control-allow-origin") == origin

    def test_07_auth_login_on_serverless_works(self):
        payload = {
            "email": "admin@aicte-india.org",
            "password": "Aicte@2026"
        }
        res = self.client.post("/api/v1/auth/login", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert len(data["access_token"].split(".")) == 3
