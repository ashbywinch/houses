"""Google OAuth endpoints for the Houses application.

Two modes:
- Auth mode (GOOGLE_CLIENT_ID set): user signs in with Google, comment
  person is derived server-side from email-to-Person mapping.
- Debug mode (GOOGLE_CLIENT_ID empty): returns ``auth_available: false``
  from the /me endpoint. The frontend falls back to the old dropdown.

Session storage is in-memory (single-process).  Tokens expire after 24h.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from houses.config import settings

# Frontend URL for redirects after OAuth — Vite dev server is 5173,
# production would come from settings or behind a proxy.
_FRONTEND_URL = "http://localhost:5173"

auth_router = APIRouter(prefix="/api/auth")

# In-memory OAuth state store — maps state_token to dict with code_verifier
_oauth_states: dict[str, dict] = {}

# In-memory session store — maps session_token to user info
_sessions: dict[str, dict[str, Any]] = {}

SESSION_MAX_AGE = timedelta(hours=24)


def _clear_expired_sessions() -> None:
    now = datetime.now(UTC)
    expired = [k for k, v in _sessions.items() if now - datetime.fromisoformat(v["created_at"]) > SESSION_MAX_AGE]
    for k in expired:
        _sessions.pop(k, None)


def _lookup_person_by_email(email: str, persons_attempt_value: Any) -> str | None:
    """Scan persons list for a matching email, return the person name or None."""
    if not persons_attempt_value:
        return None
    for p in persons_attempt_value:
        if isinstance(p, dict):
            if p.get("email") == email:
                return p.get("name")
        elif hasattr(p, "email") and p.email == email:
            return getattr(p, "name", None)
    return None


def get_session_user(request: Request) -> dict[str, Any] | None:
    """Extract session user info from the request's session_token cookie.

    Returns ``None`` if no valid session.  Expired sessions are cleaned up
    on each call.
    """
    _clear_expired_sessions()
    token = request.cookies.get("session_token")
    if not token or token not in _sessions:
        return None
    session = _sessions[token]
    # Double-check expiry
    created = datetime.fromisoformat(session["created_at"])
    if datetime.now(UTC) - created > SESSION_MAX_AGE:
        _sessions.pop(token, None)
        return None
    return session


@auth_router.get("/login")
async def login():
    """Initiate Google OAuth flow or indicate auth is unconfigured.

    Returns JSON with either ``auth_url`` (redirect browser to Google) or
    ``status: "unconfigured"``.
    """
    if not settings.google_client_id:
        return {"status": "unconfigured"}

    state = secrets.token_urlsafe(32)

    try:
        from google_auth_oauthlib.flow import Flow

        client_config = {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost:8080/api/auth/callback"],
            }
        }
        flow = Flow.from_client_config(
            client_config,
            scopes=[
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
            ],
        )
        flow.redirect_uri = "http://localhost:8080/api/auth/callback"
        authorization_url, state_from_flow = flow.authorization_url(
            access_type="online",
            include_granted_scopes="false",
            state=state,
        )
        # Persist the code_verifier so the callback can use it (PKCE)
        _oauth_states[state] = {"code_verifier": getattr(flow, "code_verifier", None) or ""}
        return {"auth_url": authorization_url}
    except ImportError:
        return {"status": "error", "detail": "google-auth libraries not installed"}


@auth_router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle the Google OAuth callback.

    Verifies state, exchanges code for token, verifies id_token, and
    creates a session.  Redirects to frontend root on success.
    """
    if error:
        return RedirectResponse(url=f"{_FRONTEND_URL}/?auth_error=" + error)

    if not code or not state:
        return RedirectResponse(url=f"{_FRONTEND_URL}/?auth_error=missing_params")

    # Verify and consume state token (prevents CSRF + replay)
    state_data = _oauth_states.pop(state, None)
    if state_data is None:
        return RedirectResponse(url=f"{_FRONTEND_URL}/?auth_error=invalid_state")

    code_verifier = state_data.get("code_verifier", "") if isinstance(state_data, dict) else ""

    try:
        from google_auth_oauthlib.flow import Flow

        client_config = {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost:8080/api/auth/callback"],
            }
        }
        flow = Flow.from_client_config(
            client_config,
            scopes=[
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
            ],
        )
        flow.redirect_uri = "http://localhost:8080/api/auth/callback"
        # Restore the PKCE code_verifier from the login request
        if code_verifier:
            flow.code_verifier = code_verifier
        flow.fetch_token(code=code)

        # Verify the id_token
        from google.auth import jwt as google_jwt
        from google.auth.transport import requests as google_requests

        id_token = getattr(flow.credentials, "id_token", None)
        if not id_token:
            return RedirectResponse(url=f"{_FRONTEND_URL}/?auth_error=no_id_token")

        request_adapter = google_requests.Request()
        id_info = google_jwt.decode(id_token, verify=False)  # we verify with verify_oauth2_token below

        # Use verify_oauth2_token for proper verification (signature, audience, expiry)
        from google.oauth2 import id_token as id_token_verifier

        id_info = id_token_verifier.verify_oauth2_token(id_token, request_adapter, settings.google_client_id)

        if not id_info.get("email_verified", False):
            return RedirectResponse(url=f"{_FRONTEND_URL}/?auth_error=email_not_verified")

        email = id_info.get("email", "")
        name = id_info.get("name", email.split("@")[0] if email else "Unknown")
        picture = id_info.get("picture", "")

        # Look up Person by email to determine superuser status
        is_superuser = False
        try:
            from houses.services_provider import get_services

            svc = get_services()
            persons_attempt = svc.persons_source.latest_attempt()
            if persons_attempt.succeeded:
                for p in persons_attempt.value_or_none() or []:
                    if isinstance(p, dict):
                        if p.get("email") == email and p.get("is_superuser"):
                            is_superuser = True
                            break
                    elif hasattr(p, "email") and p.email == email and hasattr(p, "is_superuser") and p.is_superuser:
                        is_superuser = True
                        break
        except Exception:
            pass  # Non-critical — user can still sign in, just not as superuser

        # Create session
        session_token = secrets.token_urlsafe(32)
        _sessions[session_token] = {
            "email": email,
            "name": name,
            "picture": picture,
            "is_superuser": is_superuser,
            "created_at": datetime.now(UTC).isoformat(),
        }

        # Set cookie
        is_secure = request.url.scheme == "https"
        response = RedirectResponse(url=f"{_FRONTEND_URL}/")
        response.set_cookie(
            key="session_token",
            value=session_token,
            httponly=True,
            samesite="lax",
            path="/",
            secure=is_secure,
        )
        return response

    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.exception("OAuth callback failed")
        return RedirectResponse(url=f"{_FRONTEND_URL}/?auth_error=" + str(e))


@auth_router.get("/me")
async def me(request: Request):
    """Return current authentication state.

    Always includes ``auth_available`` (true when Google OAuth is configured).
    When authenticated, also includes email, name, picture, person, is_superuser.
    """
    _clear_expired_sessions()
    auth_available = bool(settings.google_client_id)

    session_user = get_session_user(request)
    if not session_user:
        return {"authenticated": False, "auth_available": auth_available}

    # Look up associated Person by email
    person_name = None
    try:
        from houses.services_provider import get_services

        svc = get_services()
        persons_attempt = svc.persons_source.latest_attempt()
        if persons_attempt.succeeded:
            person_name = _lookup_person_by_email(session_user["email"], persons_attempt.value_or_none())
    except Exception:
        pass

    return {
        "authenticated": True,
        "auth_available": auth_available,
        "email": session_user["email"],
        "name": session_user["name"],
        "picture": session_user["picture"],
        "person": person_name,
        "is_superuser": session_user.get("is_superuser", False),
    }


@auth_router.post("/logout")
async def logout(request: Request):
    """Clear session and cookie."""
    token = request.cookies.get("session_token")
    if token and token in _sessions:
        _sessions.pop(token, None)
    response = JSONResponse(content={"status": "ok"})
    response.set_cookie(
        key="session_token",
        value="",
        httponly=True,
        samesite="lax",
        path="/",
        max_age=0,
    )
    return response
