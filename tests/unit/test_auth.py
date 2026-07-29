"""Tests for OAuth endpoints and session-based comment attribution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from houses.config import settings
from houses.server import app
from houses.services_provider import _request_services as _sp
from houses.web.auth import _make_session_cookie, _oauth_states
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


def _inject_session(
    email: str = "simon@example.com",
    is_superuser: bool = False,
    impersonating: str | None = None,
) -> str:
    """Create a signed session cookie value for testing."""
    return _make_session_cookie(
        email=email,
        name="Simon",
        picture="",
        is_superuser=is_superuser,
        impersonating=impersonating,
    )


@pytest.fixture(autouse=True)
def _clear_auth_state():
    """Clear in-memory OAuth state and client cookies between tests."""
    _oauth_states.clear()
    # TestClient accumulates cookies from set-cookie responses.  Clear them
    # so auth state from one test doesn't leak into the next.
    client.cookies.clear()


@pytest.fixture(autouse=True)
def _patch_registry():
    """Patch get_registry_property in api_router so tests with fake RIDs pass."""
    with patch("houses.web.api_router.get_registry_property", return_value=MagicMock()):
        yield


class TestLogin:
    @patch("houses.web.auth.settings.google_client_id", "test-client-id")
    @patch("houses.web.auth.settings.google_client_secret", "test-secret")
    def test_configured_returns_auth_url(self, *_):
        resp = client.get("/api/auth/login")
        assert resp.status_code == 200
        data = resp.json()
        assert "auth_url" in data
        assert data["auth_url"].startswith("https://accounts.google.com")

    @patch("houses.web.auth.settings.google_client_id", "test-client-id")
    @patch("houses.web.auth.settings.google_client_secret", "test-secret")
    def test_login_stores_code_verifier(self, *_):
        """Login stores the PKCE code_verifier in _oauth_states."""
        _oauth_states.clear()
        resp = client.get("/api/auth/login")
        assert resp.status_code == 200
        assert len(_oauth_states) == 1
        state_key = next(iter(_oauth_states))
        state_data = _oauth_states[state_key]
        assert isinstance(state_data, dict)
        assert "code_verifier" in state_data
        assert len(state_data["code_verifier"]) > 0
        _oauth_states.clear()


class TestMe:
    @patch("houses.web.auth.settings.google_client_id", "test-client-id")
    def test_not_authenticated(self, _):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json() == {"authenticated": False}

    def test_authenticated_with_session(self):
        cookie = _inject_session(email="simon@example.com")
        resp = client.get("/api/auth/me", cookies={"session": cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["email"] == "simon@example.com"
        assert data["is_superuser"] is False

    def test_authenticated_superuser(self):
        cookie = _inject_session(email="simon@example.com", is_superuser=True)
        resp = client.get("/api/auth/me", cookies={"session": cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["is_superuser"] is True

    def test_returns_impersonating(self):
        cookie = _inject_session(email="simon@example.com", is_superuser=True, impersonating="Ashby")
        resp = client.get("/api/auth/me", cookies={"session": cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["impersonating"] == "Ashby"

    def test_returns_impersonating_null_when_not_impersonating(self):
        cookie = _inject_session(email="simon@example.com", is_superuser=True)
        resp = client.get("/api/auth/me", cookies={"session": cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["impersonating"] is None

    def test_no_cookie_returns_unauthenticated(self):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False

    def test_tampered_cookie_returns_unauthenticated(self):
        cookie = _inject_session(email="simon@example.com")
        tampered = cookie[:-5] + "xxxxx"  # corrupt the signature
        resp = client.get("/api/auth/me", cookies={"session": tampered})
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False


class TestLogout:
    def test_logout_clears_cookie(self):
        cookie = _inject_session(email="simon@example.com")
        resp = client.post("/api/auth/logout", cookies={"session": cookie})
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        assert "session=" in set_cookie
        assert "Max-Age=0" in set_cookie

    def test_logout_without_session_is_idempotent(self):
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestImpersonate:
    def test_401_without_session(self):
        resp = client.post("/api/auth/impersonate", json={"person": "Ashby"})
        assert resp.status_code == 401

    def test_403_non_superuser(self):
        cookie = _inject_session(email="simon@example.com", is_superuser=False)
        resp = client.post(
            "/api/auth/impersonate",
            json={"person": "Ashby"},
            cookies={"session": cookie},
        )
        assert resp.status_code == 403

    def test_400_non_string_person(self):
        cookie = _inject_session(email="simon@example.com", is_superuser=True)
        resp = client.post(
            "/api/auth/impersonate",
            json={"person": 123},
            cookies={"session": cookie},
        )
        assert resp.status_code == 400

    def test_start_impersonating(self):
        cookie = _inject_session(email="simon@example.com", is_superuser=True)
        resp = client.post(
            "/api/auth/impersonate",
            json={"person": "Ashby"},
            cookies={"session": cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["impersonating"] == "Ashby"
        # Cookie should be updated (new set-cookie header)
        set_cookie = resp.headers.get("set-cookie", "")
        assert "session=" in set_cookie
        assert "Max-Age=0" not in set_cookie

    def test_stop_impersonating(self):
        cookie = _inject_session(email="simon@example.com", is_superuser=True, impersonating="Ashby")
        resp = client.post(
            "/api/auth/impersonate",
            json={"person": None},
            cookies={"session": cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["impersonating"] is None


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
        cookie = _inject_session()
        resp = client.get("/api/properties/all", cookies={"session": cookie})
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
        """Happy path: mocked OAuth token exchange creates a session cookie."""
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
        assert "session=" in set_cookie
        assert "Max-Age=0" not in set_cookie
        assert "httponly" in set_cookie.lower()
        assert "samesite" in set_cookie.lower()

        # Verify we can decode the cookie
        cookie_value = set_cookie.split(";")[0].split("=", 1)[1]
        from houses.web.auth import get_session_user

        class _FakeRequest:
            cookies = {"session": cookie_value}

        session = get_session_user(_FakeRequest())  # type: ignore[arg-type]
        assert session is not None
        assert session["email"] == "ashby@example.com"
        assert session["name"] == "Ashby"
        assert session["picture"] == "https://example.com/pic.jpg"

    @patch("houses.web.auth.settings.google_client_id", "test-client-id")
    @patch("houses.web.auth.settings.google_client_secret", "test-secret")
    def test_uses_full_scope_urls(self):
        """The OAuth flow uses full scope URLs to prevent scope mismatch."""
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


class TestCommentAuth:
    def test_post_401_without_session(self):
        auth_token = _enable_auth()
        try:
            resp = client.post("/api/properties/test-rid/comments", json={"text": "hello"})
        finally:
            _sp.reset(auth_token)
        assert resp.status_code == 401

    def test_post_422_empty_text(self):
        """Empty text fails Pydantic validation with 422."""
        auth_token = _enable_auth()
        session_cookie = _inject_session()
        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": ""},
                cookies={"session": session_cookie},
            )
        finally:
            _sp.reset(auth_token)
        assert resp.status_code == 422  # Pydantic validation error

    def test_post_403_non_superuser_impersonates(self):
        auth_token = _enable_auth()
        session_cookie = _inject_session(email="simon@example.com", is_superuser=False)
        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": "hello"},
                cookies={"session": session_cookie},
                headers={"X-Impersonate-Person": "Ashby"},
            )
        finally:
            _sp.reset(auth_token)
        assert resp.status_code == 403
        assert "superuser" in resp.json()["detail"]

    def test_post_400_unlinked_email(self):
        """Email doesn't match any Person — 400."""
        auth_token = _enable_auth()
        session_cookie = _inject_session(email="unlinked@example.com", is_superuser=False)
        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": "hello"},
                cookies={"session": session_cookie},
            )
        finally:
            _sp.reset(auth_token)
        assert resp.status_code == 400
        assert "not linked" in resp.json()["detail"]

    def test_post_200_normal_user(self):
        """Non-superuser with linked email can post a comment."""
        svc = make_services(
            auth_enabled=True,
            persons_source=MagicMock(
                latest_attempt=MagicMock(
                    return_value=MagicMock(
                        succeeded=True,
                        value_or_none=MagicMock(
                            return_value=[
                                {"name": "Simon", "email": "simon@example.com"},
                            ]
                        ),
                    )
                )
            ),
        )
        svc_token = _sp.set(svc)

        session_cookie = _inject_session(email="simon@example.com", is_superuser=False)
        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": "A normal comment"},
                cookies={"session": session_cookie},
            )
        finally:
            _sp.reset(svc_token)

        assert resp.status_code == 200
        data = resp.json()
        assert data["person"] == "Simon"
        assert data["text"] == "A normal comment"

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

        session_cookie = _inject_session(email="simon@example.com", is_superuser=True)
        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": "hello from Ashby"},
                cookies={"session": session_cookie},
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
        """Two different session cookies see their own session data."""
        cookie_a = _inject_session(email="alice@example.com", is_superuser=False)
        cookie_b = _inject_session(email="bob@example.com", is_superuser=True)

        resp_a = client.get("/api/auth/me", cookies={"session": cookie_a})
        resp_b = client.get("/api/auth/me", cookies={"session": cookie_b})

        assert resp_a.json()["email"] == "alice@example.com"
        assert resp_a.json()["is_superuser"] is False
        assert resp_b.json()["email"] == "bob@example.com"
        assert resp_b.json()["is_superuser"] is True
