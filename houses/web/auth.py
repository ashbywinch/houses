"""Google OAuth endpoints for the Houses application.

Users sign in with Google. Comment person is derived server-side from
email-to-Person mapping (casefolded for case-insensitive matching).

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

# In-memory OAuth state store — maps state_token to dict with code_verifier
# and created_at timestamp. Ephemeral — lost on server restart.
_oauth_states: dict[str, dict] = {}
_STATE_MAX_AGE = timedelta(minutes=10)


def _sweep_stale_states() -> None:
    """Remove OAuth state entries older than _STATE_MAX_AGE."""
    cutoff = datetime.now(UTC).timestamp() - _STATE_MAX_AGE.total_seconds()
    stale = [k for k, v in _oauth_states.items() if v.get("created_at", 0) < cutoff]
    for k in stale:
        _oauth_states.pop(k, None)


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
    """Scan persons list for a matching email (casefolded), return the person name or None."""
    if not persons_attempt_value:
        return None
    folded = email.casefold()
    for p in persons_attempt_value:
        if isinstance(p, dict):
            pe = p.get("email")
            if pe is not None and pe.casefold() == folded:
                return p.get("name")
        elif hasattr(p, "email") and p.email is not None and p.email.casefold() == folded:
            return getattr(p, "name", None)
    return None


def _is_secure(request: Request) -> bool:
    """Return True if the request arrived over HTTPS.

    When ``public_url`` starts with ``https://`` the app expects to be behind
    a TLS-terminating proxy, so ``X-Forwarded-Proto`` is trusted. Otherwise
    only ``request.url.scheme`` is used — the header could be spoofed.
    """
    if settings.public_url.startswith("https://"):
        forwarded = request.headers.get("x-forwarded-proto", "")
        if forwarded == "https":
            return True
    return request.url.scheme == "https"


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
    """Initiate Google OAuth flow.

    Returns JSON with ``auth_url`` (redirect browser to Google).
    """
    _sweep_stale_states()
    state = secrets.token_urlsafe(32)

    from houses.services_provider import get_services

    svc = get_services()
    try:
        authorization_url, code_verifier = svc.oauth_service.create_authorization_url(state)
    except ImportError:
        return {"status": "error", "detail": "google-auth libraries not installed"}

    if not code_verifier:
        return {"status": "error", "detail": "PKCE code_verifier not generated"}

    _oauth_states[state] = {
        "code_verifier": code_verifier,
        "created_at": datetime.now(UTC).timestamp(),
    }
    return {"auth_url": authorization_url}


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
    if not code_verifier:
        return RedirectResponse(url=f"{settings.frontend_url}/?auth_error=missing_code_verifier")

    from houses.services_provider import get_services

    svc = get_services()
    try:
        id_info = svc.oauth_service.exchange_code(code, code_verifier, state)

        if not id_info.get("email_verified", False):
            return RedirectResponse(url=f"{settings.frontend_url}/?auth_error=email_not_verified")

        email = id_info.get("email", "")
        folded_email = email.casefold()
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
                        pe = p.get("email")
                        if pe is not None and pe.casefold() == folded_email and p.get("is_superuser"):
                            is_superuser = True
                            break
                    elif (
                        hasattr(p, "email")
                        and p.email is not None
                        and p.email.casefold() == folded_email
                        and hasattr(p, "is_superuser")
                        and p.is_superuser
                    ):
                        is_superuser = True
                        break
        except Exception:
            pass  # Non-critical — user can still sign in, just not as superuser

        # Create signed session cookie with casefolded email
        cookie_value = _make_session_cookie(folded_email, name, picture, is_superuser)

        response = RedirectResponse(url=f"{settings.frontend_url}/")
        _set_session_cookie(response, cookie_value, _is_secure(request))
        return response

    except Exception as e:
        logger.exception("OAuth callback failed")
        return RedirectResponse(url=f"{settings.frontend_url}/?auth_error=" + quote(str(e)))


@auth_router.get("/me")
async def me(request: Request):
    """Return current authentication state.

    When authenticated, includes email, name, picture, person,
    is_superuser, and impersonating (if a superuser is impersonating someone).
    """
    session = get_session_user(request)
    if not session:
        return {"authenticated": False}

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
    response = JSONResponse(content={"status": "ok"})
    _clear_session_cookie(response, _is_secure(request))
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

    response = JSONResponse(content={"status": "ok", "impersonating": person})
    _set_session_cookie(response, new_cookie, _is_secure(request))
    return response



