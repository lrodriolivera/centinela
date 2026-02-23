"""Tests for the gateway: server, middleware, routes, streaming."""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from centinela.core.config import CentinelaConfig, reset_config
from centinela.gateway.middleware import require_auth
from centinela.gateway.streaming import sse_encode


# ─── SSE Streaming Tests ───


class TestSSEEncoding:
    def test_basic_encode(self):
        result = sse_encode('{"text":"hello"}')
        assert "data: " in result
        assert '{"text":"hello"}' in result

    def test_encode_with_event(self):
        result = sse_encode('{"text":"hi"}', event="chunk")
        assert "event: chunk" in result
        assert "data: " in result

    def test_multiline_data(self):
        result = sse_encode("line1\nline2")
        assert "data: line1" in result
        assert "data: line2" in result


# ─── Middleware Tests ───


class TestRateLimiter:
    def test_allows_under_limit(self):
        from centinela.gateway.middleware import RateLimiter

        limiter = RateLimiter(max_requests=5, window_seconds=60)
        for _ in range(5):
            assert limiter.is_allowed("10.0.0.1")

    def test_blocks_over_limit(self):
        from centinela.gateway.middleware import RateLimiter

        limiter = RateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            limiter.is_allowed("10.0.0.1")
        assert not limiter.is_allowed("10.0.0.1")

    def test_different_ips_independent(self):
        from centinela.gateway.middleware import RateLimiter

        limiter = RateLimiter(max_requests=2, window_seconds=60)
        limiter.is_allowed("10.0.0.1")
        limiter.is_allowed("10.0.0.1")
        assert not limiter.is_allowed("10.0.0.1")
        assert limiter.is_allowed("10.0.0.2")

    def test_remaining_count(self):
        from centinela.gateway.middleware import RateLimiter

        limiter = RateLimiter(max_requests=10, window_seconds=60)
        limiter.is_allowed("10.0.0.1")
        limiter.is_allowed("10.0.0.1")
        assert limiter.remaining("10.0.0.1") == 8


# ─── Fake auth override ───

async def _no_auth():
    return "test_user"


# ─── Gateway App Tests (auth disabled via dependency override) ───


@pytest.fixture
def client():
    """Create test client with auth bypassed."""
    reset_config()
    from centinela.gateway.server import create_app

    app = create_app()
    app.dependency_overrides[require_auth] = _no_auth
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestGatewayApp:
    def test_health_endpoint(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "centinela"

    def test_token_endpoint(self, client):
        resp = client.post("/api/token", json={"subject": "test_user"})
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["expires_in_minutes"] > 0

    def test_models_endpoint(self, client):
        resp = client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1

    def test_agents_endpoint(self, client):
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "general" in data

    def test_audit_endpoint(self, client):
        resp = client.get("/api/audit?limit=5")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_sessions_endpoint(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "episodic" in data

    def test_pending_endpoint(self, client):
        resp = client.get("/api/pending")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_approve_nonexistent(self, client):
        resp = client.post("/api/approve/nonexistent", json={"decided_by": "test"})
        assert resp.status_code == 404

    def test_reject_nonexistent(self, client):
        resp = client.post("/api/reject/nonexistent", json={"decided_by": "test"})
        assert resp.status_code == 404

    def test_security_headers(self, client):
        resp = client.get("/api/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("Cache-Control") == "no-store"

    def test_chat_sync_endpoint(self, client):
        mock_response = "Hola, soy Centinela"
        with patch("centinela.core.orchestrator.Orchestrator.chat", return_value=mock_response):
            resp = client.post("/api/chat/sync", json={"message": "Hola"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["text"] == mock_response


# ─── Auth Integration Tests ───


class TestAuthIntegration:
    def test_auth_required_when_enabled(self):
        reset_config()
        from centinela.gateway.server import create_app

        app = create_app()
        # No dependency override — auth is enabled by default
        c = TestClient(app)

        resp = c.get("/api/health")
        assert resp.status_code == 200

        resp = c.get("/api/models")
        assert resp.status_code == 401

    def test_auth_with_valid_token(self):
        reset_config()
        from centinela.gateway.server import create_app
        from centinela.security.auth import get_auth_manager

        app = create_app()
        c = TestClient(app)

        auth = get_auth_manager()
        token = auth.create_token(
            "test_user",
            client_ip="testclient",
            user_agent="testclient",
        )

        resp = c.get(
            "/api/models",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
