"""Tests for OAuth endpoints and session-based comment attribution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from houses.config import settings
from houses.server import app
from houses.services_provider import _request_services as _sp
from houses.web.auth import _oauth_states, _sessions
from tests.helpers import make_services

client = TestClient(app)


def _enable_auth():
    """Replace the autouse mock Services with one that has auth_enabled=True."""
    svc = _sp.get()
    if svc is not None:
        import dataclasses

        token = _sp.set(dataclasses.replace(svc, auth_enabled=True))
        return token
    token = _sp.set(make_services(auth_enabled=True))
    return token


@pytest.fixture(autouse=True)
def _clear_auth_state():
    """Clear in-memory auth state between tests."""
    _sessions.clear()
    _oauth_states.clear()


def _inject_session(email: str = "simon@example.com", is_superuser: bool = False, age_hours: float = 0) -> str:
    """Helper to create a session token for testing."""
    import secrets

    token = secrets.token_urlsafe(32)
    _sessions[token] = {
        "email": email,
        "name": "Simon",
        "picture": "",
        "is_superuser": is_superuser,
        "created_at": (datetime.now(UTC) - timedelta(hours=age_hours)).isoformat(),
    }
    return token


class TestLogin:
    def test_unconfigured(self):
        original = settings.google_client_id
        settings.google_client_id = ""
        try:
            resp = client.get("/api/auth/login")
            assert resp.status_code == 200
            assert resp.json() == {"status": "unconfigured"}
        finally:
            settings.google_client_id = original

    def test_configured_returns_auth_url(self):
        original = settings.google_client_id
        settings.google_client_id = "test-client-id"
        settings.google_client_secret = "test-secret"
        try:
            resp = client.get("/api/auth/login")
            assert resp.status_code == 200
            data = resp.json()
            assert "auth_url" in data
            assert data["auth_url"].startswith("https://accounts.google.com")
        finally:
            settings.google_client_id = original
            settings.google_client_secret = ""

    def test_login_stores_code_verifier(self):
        """Login stores the PKCE code_verifier in _oauth_states."""
        original = settings.google_client_id
        settings.google_client_id = "test-client-id"
        settings.google_client_secret = "test-secret"
        try:
            _oauth_states.clear()
            resp = client.get("/api/auth/login")
            assert resp.status_code == 200
            assert len(_oauth_states) == 1
            state_key = next(iter(_oauth_states))
            state_data = _oauth_states[state_key]
            assert isinstance(state_data, dict)
            assert "code_verifier" in state_data
            assert len(state_data["code_verifier"]) > 0
        finally:
            settings.google_client_id = original
            settings.google_client_secret = ""
            _oauth_states.clear()


class TestMe:
    def test_not_authenticated(self):
        original = settings.google_client_id
        settings.google_client_id = "test-client-id"
        try:
            resp = client.get("/api/auth/me")
            assert resp.status_code == 200
            assert resp.json() == {"authenticated": False, "auth_available": True}
        finally:
            settings.google_client_id = original

    def test_not_authenticated_auth_unavailable(self):
        original = settings.google_client_id
        settings.google_client_id = ""
        try:
            resp = client.get("/api/auth/me")
            assert resp.status_code == 200
            assert resp.json() == {"authenticated": False, "auth_available": False}
        finally:
            settings.google_client_id = original

    def test_authenticated_with_session(self):
        token = _inject_session(email="simon@example.com")
        resp = client.get("/api/auth/me", cookies={"session_token": token})
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["email"] == "simon@example.com"
        assert data["is_superuser"] is False

    def test_authenticated_superuser(self):
        token = _inject_session(email="simon@example.com", is_superuser=True)
        resp = client.get("/api/auth/me", cookies={"session_token": token})
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["is_superuser"] is True

    def test_invalid_session_token(self):
        """A bogus token that doesn't match any session returns unauthenticated."""
        resp = client.get("/api/auth/me", cookies={"session_token": "bogus-token"})
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False

    def test_session_expired(self):
        """Session older than 24h returns as unauthenticated."""
        token = _inject_session(email="simon@example.com", age_hours=25)
        resp = client.get("/api/auth/me", cookies={"session_token": token})
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is False


class TestLogout:
    def test_logout_clears_cookie_and_session(self):
        token = _inject_session(email="simon@example.com")
        resp = client.post("/api/auth/logout", cookies={"session_token": token})
        assert resp.status_code == 200
        assert token not in _sessions
        set_cookie = resp.headers.get("set-cookie", "")
        assert "session_token=" in set_cookie
        assert "Max-Age=0" in set_cookie

    def test_logout_without_session_is_idempotent(self):
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestProtectedEndpoints:
    def test_list_properties_401_without_session(self):
        resp = client.get("/api/properties/all")
        assert resp.status_code == 401

    def test_property_detail_401_without_session(self):
        resp = client.get("/api/properties/test-rid/detail")
        assert resp.status_code == 401

    def test_comments_get_401_without_session(self):
        resp = client.get("/api/properties/test-rid/comments")
        assert resp.status_code == 401

    def test_settings_401_without_session(self):
        resp = client.get("/api/settings")
        assert resp.status_code == 401

    def test_authenticated_request_succeeds(self):
        token = _inject_session()
        resp = client.get("/api/properties/all", cookies={"session_token": token})
        assert resp.status_code == 200

    def test_health_is_public(self):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_auth_me_is_public(self):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200


class TestCallback:
    @patch("houses.web.auth.settings.google_client_id", "test-client-id")
    @patch("houses.web.auth.settings.google_client_secret", "test-secret")
    def test_rejects_missing_params(self):
        resp = client.get("/api/auth/callback", follow_redirects=False)
        assert resp.status_code == 307
        location = resp.headers.get("location", "")
        assert "auth_error=missing_params" in location

    @patch("houses.web.auth.settings.google_client_id", "test-client-id")
    @patch("houses.web.auth.settings.google_client_secret", "test-secret")
    def test_rejects_invalid_state(self):
        _oauth_states["valid_state"] = {"code_verifier": "abc"}
        resp = client.get("/api/auth/callback?code=abc&state=invalid", follow_redirects=False)
        assert resp.status_code == 307
        location = resp.headers.get("location", "")
        assert "auth_error=invalid_state" in location

    @patch("houses.web.auth.settings.google_client_id", "test-client-id")
    @patch("houses.web.auth.settings.google_client_secret", "test-secret")
    def test_forwards_google_error(self):
        resp = client.get("/api/auth/callback?error=access_denied", follow_redirects=False)
        assert resp.status_code == 307
        location = resp.headers.get("location", "")
        assert "auth_error=access_denied" in location

    @patch("houses.web.auth.settings.google_client_id", "test-client-id")
    @patch("houses.web.auth.settings.google_client_secret", "test-secret")
    def test_state_replay_is_rejected(self):
        """Once consumed, the same state token cannot be reused (CSRF+replay protection)."""
        _oauth_states["s1"] = {"code_verifier": "v1"}
        with (
            patch("google_auth_oauthlib.flow.Flow.from_client_config"),
            patch("google.auth.jwt.decode"),
            patch("google.oauth2.id_token.verify_oauth2_token"),
            patch("google.auth.transport.requests.Request"),
        ):
            client.get("/api/auth/callback?code=c1&state=s1", follow_redirects=False)
        resp = client.get("/api/auth/callback?code=c2&state=s1", follow_redirects=False)
        assert resp.status_code == 307
        location = resp.headers.get("location", "")
        assert "auth_error=invalid_state" in location

    @patch("houses.web.auth.settings.google_client_id", "test-client-id")
    @patch("houses.web.auth.settings.google_client_secret", "test-secret")
    def test_success_creates_session(self):
        """Happy path: mocked OAuth token exchange creates a session and cookie."""
        _oauth_states["test_state"] = {"code_verifier": "test_verifier"}
        decoded_info = {
            "email": "ashby@example.com",
            "email_verified": True,
            "name": "Ashby",
            "picture": "https://example.com/pic.jpg",
        }

        mock_flow = MagicMock()
        mock_flow.redirect_uri = ""
        mock_flow.credentials.id_token = "eyJ.eyJ.eyJ.sig.sig"

        with (
            patch("google_auth_oauthlib.flow.Flow.from_client_config", return_value=mock_flow) as mock_factory,
            patch("google.auth.jwt.decode", return_value=decoded_info),
            patch("google.oauth2.id_token.verify_oauth2_token", return_value=decoded_info),
            patch("google.auth.transport.requests.Request"),
        ):
            resp = client.get(
                "/api/auth/callback?code=test_code&state=test_state",
                follow_redirects=False,
            )

        assert resp.status_code == 307
        assert mock_flow.fetch_token.called, "fetch_token should be called to exchange code"
        _args, kwargs = mock_factory.call_args
        passed_scopes = kwargs.get("scopes", [])
        assert "https://www.googleapis.com/auth/userinfo.email" in passed_scopes

        set_cookie = resp.headers.get("set-cookie", "")
        assert "session_token=" in set_cookie
        assert "Max-Age=0" not in set_cookie
        assert "httponly" in set_cookie.lower()
        assert "samesite" in set_cookie.lower()

        assert len(_sessions) == 1
        session = next(iter(_sessions.values()))
        assert session["email"] == "ashby@example.com"
        assert session["name"] == "Ashby"
        assert session["picture"] == "https://example.com/pic.jpg"

    def test_uses_full_scope_urls(self):
        """The OAuth flow uses full scope URLs to prevent scope mismatch."""
        original = settings.google_client_id
        settings.google_client_id = "test-client-id"
        settings.google_client_secret = "test-secret"
        try:
            with patch("google_auth_oauthlib.flow.Flow.from_client_config") as mock_flow_cls:
                mock_flow_cls.return_value.authorization_url.return_value = (
                    "https://accounts.google.com/o/oauth2/auth?scope=test",
                    "state123",
                )
                mock_flow_cls.return_value.redirect_uri = ""
                client.get("/api/auth/login")

            call_args = mock_flow_cls.call_args
            assert call_args is not None
            _args, kwargs = call_args
            passed_scopes = kwargs.get("scopes", [])
            assert "openid" in passed_scopes
            assert "https://www.googleapis.com/auth/userinfo.email" in passed_scopes
            assert "https://www.googleapis.com/auth/userinfo.profile" in passed_scopes
            assert "email" not in passed_scopes
        finally:
            settings.google_client_id = original
            settings.google_client_secret = ""


class TestCommentAuth:
    def test_post_401_without_session(self):
        auth_token = _enable_auth()
        try:
            resp = client.post("/api/properties/test-rid/comments", json={"text": "hello"})
        finally:
            _sp.reset(auth_token)
        assert resp.status_code == 401

    def test_post_400_empty_text(self):
        auth_token = _enable_auth()
        session_token = _inject_session()
        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": ""},
                cookies={"session_token": session_token},
            )
        finally:
            _sp.reset(auth_token)
        assert resp.status_code == 400
        assert "text is required" in resp.json()["detail"]

    def test_post_403_non_superuser_impersonates(self):
        auth_token = _enable_auth()
        session_token = _inject_session(email="simon@example.com", is_superuser=False)
        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": "hello"},
                cookies={"session_token": session_token},
                headers={"X-Impersonate-Person": "Ashby"},
            )
        finally:
            _sp.reset(auth_token)
        assert resp.status_code == 403
        assert "superuser" in resp.json()["detail"]

    def test_post_400_unlinked_email(self):
        """Email doesn't match any Person — 400."""
        auth_token = _enable_auth()
        session_token = _inject_session(email="unlinked@example.com", is_superuser=False)
        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": "hello"},
                cookies={"session_token": session_token},
            )
        finally:
            _sp.reset(auth_token)
        assert resp.status_code == 400
        assert "not linked" in resp.json()["detail"]

    def test_post_200_superuser_impersonates(self):
        """Superuser with impersonation header succeeds with resolved person."""
        svc = make_services(
            auth_enabled=True,
            persons_source=MagicMock(
                latest_attempt=MagicMock(
                    return_value=MagicMock(
                        succeeded=True,
                        value_or_none=MagicMock(
                            return_value=[
                                {"name": "Ashby", "email": "ashby@example.com"},
                            ]
                        ),
                    )
                )
            ),
        )
        svc_token = _sp.set(svc)

        session_token = _inject_session(email="simon@example.com", is_superuser=True)
        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": "hello from Ashby"},
                cookies={"session_token": session_token},
                headers={"X-Impersonate-Person": "Ashby"},
            )
        finally:
            _sp.reset(svc_token)

        assert resp.status_code == 200
        data = resp.json()
        assert data["person"] == "Ashby"
        assert data["text"] == "hello from Ashby"


class TestSessionIsolation:
    def test_concurrent_sessions_independent(self):
        """Two different session tokens see their own session data."""
        token_a = _inject_session(email="alice@example.com", is_superuser=False)
        token_b = _inject_session(email="bob@example.com", is_superuser=True)

        resp_a = client.get("/api/auth/me", cookies={"session_token": token_a})
        resp_b = client.get("/api/auth/me", cookies={"session_token": token_b})

        assert resp_a.json()["email"] == "alice@example.com"
        assert resp_a.json()["is_superuser"] is False
        assert resp_b.json()["email"] == "bob@example.com"
        assert resp_b.json()["is_superuser"] is True
