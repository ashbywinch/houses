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

    from houses.web.auth import _sessions

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
            # Should have exactly one state entry with a code_verifier
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

    def test_session_expires_after_24h(self):
        token = _inject_session(email="simon@example.com", age_hours=25)
        resp = client.get("/api/auth/me", cookies={"session_token": token})
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is False


class TestLogout:
    def test_logout_clears_cookie(self):
        token = _inject_session(email="simon@example.com")
        resp = client.post("/api/auth/logout", cookies={"session_token": token})
        assert resp.status_code == 200
        # Session should be cleared
        assert token not in _sessions
        # Cookie should be expired
        set_cookie = resp.headers.get("set-cookie", "")
        assert "session_token=" in set_cookie
        assert "Max-Age=0" in set_cookie


class TestProtectedEndpoints:
    def test_list_properties_401_without_session(self):
        resp = client.get("/api/properties/all")
        assert resp.status_code == 401

    def test_property_detail_401_without_session(self):
        resp = client.get("/api/properties/test-rid/detail")
        assert resp.status_code == 401

    def test_settings_401_without_session(self):
        resp = client.get("/api/settings")
        assert resp.status_code == 401

    def test_list_properties_200_with_session(self):
        token = _inject_session()
        resp = client.get("/api/properties/all", cookies={"session_token": token})
        assert resp.status_code == 200

    def test_health_is_public(self):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_auth_me_is_public(self):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200


class TestCommentAuth:
    def test_comment_post_401_without_session(self):
        auth_token = _enable_auth()
        try:
            resp = client.post("/api/properties/test-rid/comments", json={"text": "hello"})
        finally:
            _sp.reset(auth_token)
        assert resp.status_code == 401
        assert "Authentication" in resp.json()["detail"]

    def test_comment_post_403_non_superuser_impersonates(self):
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

    def test_comment_post_400_unlinked_email(self):
        """Email doesn't match any Person — 400."""
        auth_token = _enable_auth()
        session_token = _inject_session(email="unlinked@example.com", is_superuser=True)
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

    def test_comment_post_200_superuser_impersonates(self):
        """Superuser with impersonation header succeeds."""
        import secrets

        from houses.web.auth import _sessions

        # Enable auth mode
        auth_token = _enable_auth()

        # Create a custom Services with a persons_source that has linked email
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

        # Inject a session for simon (superuser)
        session_token = secrets.token_urlsafe(32)
        _sessions[session_token] = {
            "email": "simon@example.com",
            "name": "Simon",
            "picture": "",
            "is_superuser": True,
            "created_at": datetime.now(UTC).isoformat(),
        }

        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": "hello from Ashby"},
                cookies={"session_token": session_token},
                headers={"X-Impersonate-Person": "Ashby"},
            )
        finally:
            _sp.reset(svc_token)
            _sp.reset(auth_token)

        assert resp.status_code == 200
        data = resp.json()
        assert data["person"] == "Ashby"
        assert data["text"] == "hello from Ashby"


class TestCallback:
    @patch("houses.web.auth.settings.google_client_id", "test-client-id")
    @patch("houses.web.auth.settings.google_client_secret", "test-secret")
    def test_callback_rejects_missing_params(self):
        resp = client.get("/api/auth/callback", follow_redirects=False)
        assert resp.status_code == 307  # RedirectResponse
        location = resp.headers.get("location", "")
        assert "http://localhost:5173/" in location
        assert "auth_error=missing_params" in location

    def test_callback_rejects_invalid_state(self):
        _oauth_states["valid_state"] = {}
        resp = client.get("/api/auth/callback?code=abc&state=invalid_state", follow_redirects=False)
        assert resp.status_code == 307  # RedirectResponse
        location = resp.headers.get("location", "")
        assert "http://localhost:5173/" in location
        assert "auth_error=invalid_state" in location

    @patch("houses.web.auth.settings.google_client_id", "test-client-id")
    @patch("houses.web.auth.settings.google_client_secret", "test-secret")
    def test_callback_forwards_google_error(self):
        """Google's error query param is forwarded to frontend."""
        resp = client.get("/api/auth/callback?error=access_denied", follow_redirects=False)
        assert resp.status_code == 307
        location = resp.headers.get("location", "")
        assert "http://localhost:5173/" in location
        assert "auth_error=access_denied" in location

    @patch("houses.web.auth.settings.google_client_id", "test-client-id")
    @patch("houses.web.auth.settings.google_client_secret", "test-secret")
    def test_uses_full_scope_urls(self):
        """The OAuth flow uses full scope URLs to prevent scope mismatch.

        Google expands shorthand scopes ("email" →
        "https://www.googleapis.com/auth/userinfo.email") during token
        exchange. If the Flow was created with shorthands, ``fetch_token``
        raises a scope mismatch error. Using full URLs in both
        ``authorization_url`` and ``fetch_token`` avoids this.
        """
        from unittest.mock import patch as _patch

        with _patch("google_auth_oauthlib.flow.Flow.from_client_config") as mock_flow_cls:
            mock_flow_cls.return_value.authorization_url.return_value = (
                "https://accounts.google.com/o/oauth2/auth?scope=test",
                "state123",
            )
            mock_flow_cls.return_value.redirect_uri = ""

            resp = client.get("/api/auth/login")

        assert resp.status_code == 200

        # Grab the scopes that were passed to from_client_config
        call_args = mock_flow_cls.call_args
        assert call_args is not None, "Flow.from_client_config was not called"
        _args, kwargs = call_args
        passed_scopes = kwargs.get("scopes", [])

        assert "openid" in passed_scopes
        assert "https://www.googleapis.com/auth/userinfo.email" in passed_scopes, (
            f"Expected full email scope URL, got scopes: {passed_scopes}"
        )
        assert "https://www.googleapis.com/auth/userinfo.profile" in passed_scopes, (
            f"Expected full profile scope URL, got scopes: {passed_scopes}"
        )
        # Shorthand versions should NOT be present
        assert "email" not in passed_scopes, f"Shorthand 'email' should not be used: {passed_scopes}"
        assert "profile" not in passed_scopes, f"Shorthand 'profile' should not be used: {passed_scopes}"
