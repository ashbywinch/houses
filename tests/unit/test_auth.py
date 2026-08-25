"""Tests for OAuth endpoints and session-based comment attribution."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from houses.server import app
from houses.services_provider import _request_services as _sp
from houses.services_provider import get_services
from houses.web.auth import _make_session_cookie, _oauth_states, _OAuthState, get_session_user
from tests.helpers import FakeOAuthService, make_services

client = TestClient(app)


class _FakeProperty:
    """Minimal property stand-in for auth tests that need a valid RID."""

    __slots__ = ()

    async def to_json_summary(self) -> dict:
        return {}

    async def to_json(self) -> dict:
        return {}

    async def to_json_detail(self) -> dict:
        return {}


@pytest.fixture(autouse=True)
def _fake_registry():
    """Insert a minimal fake property so endpoints that validate RIDs don't 404."""
    registry = get_services().property_registry
    registry.register("test-rid", _FakeProperty())
    try:
        yield
    finally:
        registry.clear()


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
def _fake_oauth():
    """Inject FakeOAuthService so tests don't call real Google APIs."""
    token = _sp.set(make_services(oauth_service=FakeOAuthService()))
    try:
        yield
    finally:
        _sp.reset(token)


class TestLogin:
    def test_configured_returns_auth_url(self):
        resp = client.get("/api/auth/login")
        assert resp.status_code == 200
        data = resp.json()
        assert "auth_url" in data
        assert data["auth_url"].startswith("https://accounts.google.com")

    def test_login_stores_code_verifier(self):
        """Login stores the PKCE code_verifier in _oauth_states."""
        _oauth_states.clear()
        resp = client.get("/api/auth/login")
        assert resp.status_code == 200
        assert len(_oauth_states) == 1
        state_key = next(iter(_oauth_states))
        state_data = _oauth_states[state_key]
        assert isinstance(state_data, _OAuthState)
        assert len(state_data.code_verifier) > 0
        _oauth_states.clear()


class TestMe:
    def test_not_authenticated(self):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json() == {"authenticated": False}

    def test_authenticated_with_session(self):
        cookie = _inject_session(email="simon@example.com")
        client.cookies.set("session", cookie)
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["email"] == "simon@example.com"
        assert data["is_superuser"] is False

    def test_authenticated_superuser(self):
        cookie = _inject_session(email="simon@example.com", is_superuser=True)
        client.cookies.set("session", cookie)
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["is_superuser"] is True

    def test_returns_impersonating(self):
        cookie = _inject_session(email="simon@example.com", is_superuser=True, impersonating="Ashby")
        client.cookies.set("session", cookie)
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["impersonating"] == "Ashby"

    def test_returns_impersonating_null_when_not_impersonating(self):
        cookie = _inject_session(email="simon@example.com", is_superuser=True)
        client.cookies.set("session", cookie)
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["impersonating"] is None

    def test_no_cookie_returns_unauthenticated(self):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False

    def test_tampered_cookie_returns_unauthenticated(self):
        cookie = _inject_session(email="simon@example.com")
        client.cookies.set("session", cookie)
        tampered = cookie[:-5] + "xxxxx"  # corrupt the signature
        client.cookies.set("session", tampered)
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False


class TestLiveSuperuserDerivation:
    """is_superuser is re-derived from LIVE settings, not the cookie
    snapshot — a promotion in Settings applies without re-login."""

    def _push_superuser_person(self, email: str) -> None:
        from dataclasses import replace

        from houses.nodes.settings import make_default_persons
        from houses.services_provider import get_services

        svc = get_services()
        persons = [
            replace(p, email=email, is_superuser=True) if p.name == "Simon" else p
            for p in make_default_persons()
        ]
        svc.persons_source.push(persons, "test")

    def test_me_derives_superuser_from_live_settings(self):
        """A cookie minted BEFORE the promotion (is_superuser=False) must
        still report true when the live settings say the person is one."""
        self._push_superuser_person(email="simon@example.com")
        cookie = _inject_session(email="simon@example.com", is_superuser=False)
        client.cookies.set("session", cookie)
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["is_superuser"] is True

    def test_me_keeps_false_when_settings_do_not_promote(self):
        """No live superuser flag → the cookie snapshot stands."""
        cookie = _inject_session(email="simon@example.com", is_superuser=False)
        client.cookies.set("session", cookie)
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["is_superuser"] is False

    def test_impersonate_allowed_with_stale_cookie_and_live_flag(self):
        """The superuser-only impersonate endpoint must accept a session
        whose cookie predates the promotion, when live settings promote."""
        self._push_superuser_person(email="simon@example.com")
        cookie = _inject_session(email="simon@example.com", is_superuser=False)
        client.cookies.set("session", cookie)
        resp = client.post(
            "/api/auth/impersonate",
            json={"person": "Ashby"},
        )
        assert resp.status_code == 200, f"got {resp.status_code}: {resp.text[:200]}"


class TestLogout:
    def test_logout_clears_cookie(self):
        cookie = _inject_session(email="simon@example.com")
        client.cookies.set("session", cookie)
        resp = client.post("/api/auth/logout")
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
        client.cookies.set("session", cookie)
        resp = client.post(
            "/api/auth/impersonate",
            json={"person": "Ashby"},
        )
        assert resp.status_code == 403

    def test_400_non_string_person(self):
        cookie = _inject_session(email="simon@example.com", is_superuser=True)
        client.cookies.set("session", cookie)
        resp = client.post(
            "/api/auth/impersonate",
            json={"person": 123},
        )
        assert resp.status_code == 400

    def test_start_impersonating(self):
        cookie = _inject_session(email="simon@example.com", is_superuser=True)
        client.cookies.set("session", cookie)
        resp = client.post(
            "/api/auth/impersonate",
            json={"person": "Ashby"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["impersonating"] == "Ashby"
        # Cookie should be updated (new set-cookie header)
        set_cookie = resp.headers.get("set-cookie", "")
        assert "session=" in set_cookie
        assert "Max-Age=0" not in set_cookie

    def test_cannot_impersonate_a_child(self):
        """Superusers may act as any ADULT — children are off limits."""
        cookie = _inject_session(email="simon@example.com", is_superuser=True)
        client.cookies.set("session", cookie)
        resp = client.post(
            "/api/auth/impersonate",
            json={"person": "George"},  # a child in the family defaults
        )
        assert resp.status_code == 400
        assert "child" in resp.json()["detail"].lower()

    def test_stop_impersonating(self):
        cookie = _inject_session(email="simon@example.com", is_superuser=True, impersonating="Ashby")
        client.cookies.set("session", cookie)
        resp = client.post(
            "/api/auth/impersonate",
            json={"person": None},
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
        client.cookies.set("session", cookie)
        resp = client.get("/api/properties/all")
        assert resp.status_code == 200

    def test_health_is_public(self):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_auth_me_is_public(self):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200


class TestCallback:
    def test_rejects_missing_params(self):
        resp = client.get("/api/auth/callback", follow_redirects=False)
        assert resp.status_code == 307
        location = resp.headers.get("location", "")
        assert "auth_error=missing_params" in location

    def test_rejects_invalid_state(self):
        _oauth_states["valid_state"] = _OAuthState(code_verifier="abc", created_at=0.0)
        resp = client.get("/api/auth/callback?code=abc&state=invalid", follow_redirects=False)
        assert resp.status_code == 307
        location = resp.headers.get("location", "")
        assert "auth_error=invalid_state" in location

    def test_forwards_google_error(self):
        resp = client.get("/api/auth/callback?error=access_denied", follow_redirects=False)
        assert resp.status_code == 307
        location = resp.headers.get("location", "")
        assert "auth_error=access_denied" in location

    def test_state_replay_is_rejected(self):
        """Once consumed, the same state token cannot be reused (CSRF+replay protection)."""
        _oauth_states["s1"] = _OAuthState(code_verifier="v1", created_at=0.0)
        # First call succeeds with FakeOAuthService
        client.get("/api/auth/callback?code=c1&state=s1", follow_redirects=False)
        # Second call with same state should be rejected
        resp = client.get("/api/auth/callback?code=c2&state=s1", follow_redirects=False)
        assert resp.status_code == 307
        location = resp.headers.get("location", "")
        assert "auth_error=invalid_state" in location

    def test_success_creates_session(self):
        """Happy path: FakeOAuthService returns id_info, creates session cookie."""
        _oauth_states["test_state"] = _OAuthState(code_verifier="test_verifier", created_at=0.0)

        id_info = {
            "email": "ashby@example.com",
            "email_verified": True,
            "name": "Ashby",
            "picture": "https://example.com/pic.jpg",
        }
        token = _sp.set(
            make_services(
                oauth_service=FakeOAuthService(id_info=id_info),
            )
        )
        try:
            resp = client.get(
                "/api/auth/callback?code=test_code&state=test_state",
                follow_redirects=False,
            )
        finally:
            _sp.reset(token)

        assert resp.status_code == 307

        set_cookie = resp.headers.get("set-cookie", "")
        assert "session=" in set_cookie
        assert "Max-Age=0" not in set_cookie
        assert "httponly" in set_cookie.lower()
        assert "samesite" in set_cookie.lower()

        # Verify we can decode the cookie
        cookie_value = set_cookie.split(";")[0].split("=", 1)[1]

        class _FakeRequest:
            cookies = {"session": cookie_value}

        session = get_session_user(_FakeRequest())  # type: ignore[arg-type]  # request is annotated starlette Request but the function only reads .cookies — the duck-typed _FakeRequest supplies exactly that
        assert session is not None
        assert session["email"] == "ashby@example.com"
        assert session["name"] == "Ashby"
        assert session["picture"] == "https://example.com/pic.jpg"

    def test_uses_full_scope_urls(self):
        """The login endpoint returns an auth_url."""
        resp = client.get("/api/auth/login")
        assert resp.status_code == 200
        data = resp.json()
        assert "auth_url" in data


class TestDevice:
    """POST /api/auth/device — headless login via Google OAuth device flow."""

    def test_rejects_missing_token(self):
        resp = client.post("/api/auth/device", json={})
        assert resp.status_code == 400

    def test_rejects_non_object_body(self):
        resp = client.post("/api/auth/device", json=["x"])
        assert resp.status_code == 400
        resp2 = client.post(
            "/api/auth/device",
            content="{not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp2.status_code == 400

    def test_rejects_invalid_token(self):
        from houses.settings import settings

        token = _sp.set(
            make_services(oauth_service=FakeOAuthService(verify_error=ValueError("bad")))
        )
        saved = settings.device_client_id
        settings.device_client_id = "fake-device-client"
        try:
            resp = client.post("/api/auth/device", json={"id_token": "garbage"})
            assert resp.status_code == 401
        finally:
            _sp.reset(token)
            settings.device_client_id = saved

    def test_mints_session_cookie(self):
        # autouse _fake_oauth fixture provides FakeOAuthService (id_info ashby@example.com)
        from houses.settings import settings

        saved = settings.device_client_id
        settings.device_client_id = "fake-device-client"
        try:
            resp = client.post("/api/auth/device", json={"id_token": "real-looking"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["authenticated"] is True
            assert data["email"] == "ashby@example.com"
            assert "session_cookie" not in data  # cookie lives in Set-Cookie only
            assert resp.headers.get("cache-control") == "no-store"
            cookie = resp.cookies.get("session")
            assert cookie

            # the minted cookie is a real session
            me = client.get("/api/auth/me")
            assert me.json()["authenticated"] is True
        finally:
            settings.device_client_id = saved

    def test_503_when_device_client_not_configured(self):
        from houses.settings import settings

        saved = settings.device_client_id
        settings.device_client_id = ""
        try:
            resp = client.post("/api/auth/device", json={"id_token": "whatever"})
            assert resp.status_code == 503
        finally:
            settings.device_client_id = saved

    def test_503_when_identity_provider_unreachable(self):
        """A transient Google transport failure is retryable 503, not a 401."""
        from google.auth.exceptions import TransportError

        from houses.settings import settings

        token = _sp.set(
            make_services(oauth_service=FakeOAuthService(verify_error=TransportError("no network")))
        )
        saved = settings.device_client_id
        settings.device_client_id = "fake-device-client"
        try:
            resp = client.post("/api/auth/device", json={"id_token": "real-looking"})
            assert resp.status_code == 503
        finally:
            _sp.reset(token)
            settings.device_client_id = saved

    def test_rejects_cross_origin_request(self):
        """A malicious page must not be able to mint a session for the victim."""
        from houses.settings import settings

        saved = settings.device_client_id
        settings.device_client_id = "fake-device-client"
        try:
            resp = client.post(
                "/api/auth/device",
                json={"id_token": "real-looking"},
                headers={"Origin": "https://evil.example"},
            )
            assert resp.status_code == 403
        finally:
            settings.device_client_id = saved

    def test_accepts_app_origin(self):
        """Requests from the app's own origins (or no Origin, e.g. the CLI) pass."""
        from houses.settings import settings

        saved = settings.device_client_id
        settings.device_client_id = "fake-device-client"
        try:
            resp = client.post(
                "/api/auth/device",
                json={"id_token": "real-looking"},
                headers={"Origin": settings.frontend_url},
            )
            assert resp.status_code == 200
        finally:
            settings.device_client_id = saved


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
        client.cookies.set("session", session_cookie)
        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": ""},
            )
        finally:
            _sp.reset(auth_token)
        assert resp.status_code == 422  # Pydantic validation error

    def test_post_403_non_superuser_impersonates(self):
        auth_token = _enable_auth()
        session_cookie = _inject_session(email="simon@example.com", is_superuser=False)
        client.cookies.set("session", session_cookie)
        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": "hello"},
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
        client.cookies.set("session", session_cookie)
        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": "hello"},
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
        client.cookies.set("session", session_cookie)
        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": "A normal comment"},
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
        client.cookies.set("session", session_cookie)
        try:
            resp = client.post(
                "/api/properties/test-rid/comments",
                json={"text": "hello from Ashby"},
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

        client.cookies.set("session", cookie_a)
        resp_a = client.get("/api/auth/me")
        client.cookies.set("session", cookie_b)
        resp_b = client.get("/api/auth/me")

        assert resp_a.json()["email"] == "alice@example.com"
        assert resp_a.json()["is_superuser"] is False
        assert resp_b.json()["email"] == "bob@example.com"
        assert resp_b.json()["is_superuser"] is True
