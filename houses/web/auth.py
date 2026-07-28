"""Google OAuth endpoints for the Houses application.

Two modes:
- Auth mode (GOOGLE_CLIENT_ID set): user signs in with Google, comment
  person is derived server-side from email-to-Person mapping.
- Debug mode (GOOGLE_CLIENT_ID empty): returns ``auth_available: false``
  from the /me endpoint. The frontend falls back to the old dropdown.

Session storage uses signed cookies (itsdangerous.URLSafeTimedSerializer).
The cookie payload contains ``{email, name, picture, is_superuser, impersonating}``.
Cookies are valid for 30 days and survive server restarts.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from houses.config import settings

_SESSION_MAX_AGE = timedelta(days=30)

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/api/auth")

# In-memory OAuth state store — maps state_token to dict with code_verifier.
# Ephemeral — lost on server restart, user retries the OAuth flow.
_oauth_states: dict[str, dict] = {}


def _get_serializer() -> URLSafeTimedSerializer:
    """Return a serializer for signing session cookies."""
    return URLSafeTimedSerializer(settings.session_secret, salt="auth-session")


def get_session_user(request: Request) -> dict[str, Any] | None:
    """Extract session user info from the signed ``session`` cookie.

    Returns ``None`` if the cookie is missing, tampered, or expired.
    Survives server restarts — the signed cookie is self-contained.
    """
    cookie = request.cookies.get("session")
    if not cookie:
        return None
    try:
        return _get_serializer().loads(cookie, max_age=int(_SESSION_MAX_AGE.total_seconds()))
    except (BadSignature, SignatureExpired):
        return None


def _make_session_cookie(
    email: str,
    name: str,
    picture: str,
    is_superuser: bool,
    impersonating: str | None = None,
) -> str:
    """Create a signed session cookie value."""
    payload: dict[str, Any] = {
        "email": email,
        "name": name,
        "picture": picture,
        "is_superuser": is_superuser,
        "impersonating": impersonating,
    }
    return _get_serializer().dumps(payload)


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


def _set_session_cookie(response, cookie_value: str, secure: bool) -> None:
    """Set the signed session cookie on the response."""
    response.set_cookie(
        key="session",
        value=cookie_value,
        httponly=True,
        samesite="lax",
        path="/",
        secure=secure,
        max_age=int(_SESSION_MAX_AGE.total_seconds()),
    )


def _clear_session_cookie(response, secure: bool) -> None:
    """Clear the session cookie."""
    response.set_cookie(
        key="session",
        value="",
        httponly=True,
        samesite="lax",
        path="/",
        secure=secure,
        max_age=0,
    )


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
                "redirect_uris": [settings.public_url.rstrip("/") + "/api/auth/callback"],
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
        flow.redirect_uri = settings.public_url.rstrip("/") + "/api/auth/callback"
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
    creates a signed session cookie.  Redirects to frontend root on success.
    """
    if error:
        return RedirectResponse(url=f"{settings.frontend_url}/?auth_error=" + quote(error))

    if not code or not state:
        return RedirectResponse(url=f"{settings.frontend_url}/?auth_error=missing_params")

    # Verify and consume state token (prevents CSRF + replay)
    state_data = _oauth_states.pop(state, None)
    if state_data is None:
        return RedirectResponse(url=f"{settings.frontend_url}/?auth_error=invalid_state")

    code_verifier = state_data.get("code_verifier", "") if isinstance(state_data, dict) else ""

    try:
        from google_auth_oauthlib.flow import Flow

        client_config = {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.public_url.rstrip("/") + "/api/auth/callback"],
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
        flow.redirect_uri = settings.public_url.rstrip("/") + "/api/auth/callback"
        # Restore the PKCE code_verifier from the login request
        if code_verifier:
            flow.code_verifier = code_verifier
        flow.fetch_token(code=code)

        # Verify the id_token
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as id_token_verifier

        id_token = getattr(flow.credentials, "id_token", None)
        if not id_token:
            return RedirectResponse(url=f"{settings.frontend_url}/?auth_error=no_id_token")

        request_adapter = google_requests.Request()
        id_info = id_token_verifier.verify_oauth2_token(id_token, request_adapter, settings.google_client_id)

        if not id_info.get("email_verified", False):
            return RedirectResponse(url=f"{settings.frontend_url}/?auth_error=email_not_verified")

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

        # Create signed session cookie
        cookie_value = _make_session_cookie(email, name, picture, is_superuser)

        is_secure = request.url.scheme == "https"
        response = RedirectResponse(url=f"{settings.frontend_url}/")
        _set_session_cookie(response, cookie_value, is_secure)
        return response

    except Exception as e:
        logger.exception("OAuth callback failed")
        return RedirectResponse(url=f"{settings.frontend_url}/?auth_error=" + quote(str(e)))


@auth_router.get("/me")
async def me(request: Request):
    """Return current authentication state.

    Always includes ``auth_available`` (true when Google OAuth is configured).
    When authenticated, also includes email, name, picture, person,
    is_superuser, and impersonating (if a superuser is impersonating someone).
    """
    auth_available = bool(settings.google_client_id)

    session = get_session_user(request)
    if not session:
        return {"authenticated": False, "auth_available": auth_available}

    # Look up associated Person by email
    person_name = None
    try:
        from houses.services_provider import get_services

        svc = get_services()
        persons_attempt = svc.persons_source.latest_attempt()
        if persons_attempt.succeeded:
            person_name = _lookup_person_by_email(session["email"], persons_attempt.value_or_none())
    except Exception:
        pass

    return {
        "authenticated": True,
        "auth_available": auth_available,
        "email": session["email"],
        "name": session["name"],
        "picture": session.get("picture", ""),
        "person": person_name,
        "is_superuser": session.get("is_superuser", False),
        "impersonating": session.get("impersonating"),
    }


@auth_router.post("/logout")
async def logout(request: Request):
    """Clear session cookie."""
    is_secure = request.url.scheme == "https"
    response = JSONResponse(content={"status": "ok"})
    _clear_session_cookie(response, is_secure)
    return response


@auth_router.post("/impersonate")
async def impersonate(request: Request, body: dict):
    """Set or clear impersonation for a superuser.

    Body::

        { "person": "Simon" }   # start impersonating
        { "person": null }      # stop impersonating

    Returns a new session cookie with the ``impersonating`` field updated.
    Survives server restarts.
    """
    session = get_session_user(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not session.get("is_superuser"):
        raise HTTPException(status_code=403, detail="Only superusers can impersonate")

    person = body.get("person")
    if person is not None and not isinstance(person, str):
        raise HTTPException(status_code=400, detail="person must be a string or null")

    new_cookie = _make_session_cookie(
        email=session["email"],
        name=session["name"],
        picture=session.get("picture", ""),
        is_superuser=True,
        impersonating=person,
    )

    is_secure = request.url.scheme == "https"
    response = JSONResponse(content={"status": "ok", "impersonating": person})
    _set_session_cookie(response, new_cookie, is_secure)
    return response
