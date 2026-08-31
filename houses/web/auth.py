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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from google.auth.exceptions import TransportError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from houses.services_provider import get_services
from houses.settings import settings

SESSION_MAX_AGE = timedelta(days=30)

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class SessionMint:
    """(user_payload, cookie_value) from _build_session — named so the
    web callback and device-flow endpoint read the fields by meaning."""

    payload: dict[str, Any]
    cookie_value: str



@dataclass(frozen=True)
class _OAuthState:
    """Per-login PKCE state: the code_verifier and when it was minted."""

    code_verifier: str
    created_at: float



auth_router = APIRouter(prefix="/api/auth")

# In-memory OAuth state store — maps state_token to its PKCE record.
# Ephemeral — lost on server restart.
# lucidlint: ignore global-state bounded module cache/state — single writer, deliberate
_oauth_states: dict[str, _OAuthState] = {}
_STATE_MAX_AGE = timedelta(minutes=10)
_STATE_MAX_ENTRIES = 100
_OAUTH_STATE_BYTES = 32


def _sweep_stale_states() -> None:
    """Remove OAuth state entries older than _STATE_MAX_AGE."""
    cutoff = datetime.now(UTC).timestamp() - _STATE_MAX_AGE.total_seconds()
    stale = [k for k, v in _oauth_states.items() if v.created_at < cutoff]
    for k in stale:
        _oauth_states.pop(k, None)




def get_serializer() -> URLSafeTimedSerializer:
    """Return a serializer for signing session cookies."""
    return URLSafeTimedSerializer(settings.session_secret, salt="auth-session")


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def get_session_user(request: Request) -> dict[str, Any] | None:
    """Extract session user info from the signed ``session`` cookie.

    Returns ``None`` if the cookie is missing, tampered, or expired.
    Survives server restarts — the signed cookie is self-contained.
    """
    cookie = request.cookies.get("session")
    if not cookie:
        return None
    try:
        return get_serializer().loads(cookie, max_age=int(SESSION_MAX_AGE.total_seconds()))
    except (BadSignature, SignatureExpired):
        return None


def _person_superuser_flag(email: str, persons_attempt_value: Any) -> bool | None:
    """The LIVE is_superuser flag for *email* from the settings persons.

    Returns ``None`` when the persons source is unavailable or the email
    matches no person — the caller falls back to the cookie's snapshot.
    """
    folded = email.casefold()
    for p in persons_attempt_value or []:
        if isinstance(p, dict):
            pe = p.get("email")
            if pe is not None and pe.casefold() == folded:
                return bool(p.get("is_superuser"))
        elif hasattr(p, "email") and p.email is not None and p.email.casefold() == folded:
            return bool(getattr(p, "is_superuser", False))
    return None


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def effective_session_user(request: Request) -> dict[str, Any] | None:
    """The session user with is_superuser re-derived from LIVE settings.

    The cookie bakes is_superuser at login (30-day validity) — a
    promotion in Settings would otherwise leave the user's existing
    session without the flag until re-login. The cookie still carries
    identity; the privilege is looked up fresh each request and falls
    back to the cookie snapshot only when settings are unavailable.
    """
    session = get_session_user(request)
    if not session:
        return None
    try:
        svc = get_services()
        persons_attempt = svc.persons_source.latest_attempt()
        if persons_attempt.succeeded:
            live = _person_superuser_flag(session.get("email", ""), persons_attempt.value_or_none())
            if live is not None and live != session.get("is_superuser", False):
                session = {**session, "is_superuser": live}
    # lucidlint: ignore broad-except live superuser re-derivation failure logs and returns the session unchanged
    except Exception:
        logger.exception("Failed to re-derive superuser flag from settings")
        return session
    return session


def _current_person_name(session: Mapping[str, Any]) -> str | None:
    """The Person display name for the session email, or None when unknown/failed."""
    svc = get_services()
    try:
        persons_attempt = svc.persons_source.latest_attempt()
        if persons_attempt.succeeded:
            return _lookup_person_by_email(session["email"], persons_attempt.value_or_none())
    # lucidlint: ignore broad-except deliberate degrade — person-name lookup failure returns None
    except Exception:
        logger.exception("Failed to look up person name")
        return None
    return None


def _make_session_cookie(
    email: str,
    name: str,
    picture: str,
    is_superuser: bool,
    impersonating: str | None = None,
) -> str:
    """Create a signed session cookie value."""
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    payload: dict[str, Any] = {
        "email": email,
        "name": name,
        "picture": picture,
        "is_superuser": is_superuser,
        "impersonating": impersonating,
    }
    return get_serializer().dumps(payload)


def _build_session(id_info: Mapping[str, Any]) -> SessionMint:
    """Return (user_payload, session_cookie_value) for verified Google id_info.

    Shared by the web callback and the device-flow endpoint so both mint
    identical sessions.
    """
    email = id_info.get("email", "")
    folded_email = email.casefold()
    name = id_info.get("name", email.split("@")[0] if email else "Unknown")
    picture = id_info.get("picture", "")

    # Look up Person by email to determine superuser status
    is_superuser = False
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

    cookie_value = _make_session_cookie(folded_email, name, picture, is_superuser)
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    payload = {
        "email": folded_email,
        "name": name,
        "picture": picture,
        "is_superuser": is_superuser,
        "impersonating": None,
    }
    return SessionMint(payload, cookie_value)


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


def _origin_allowed(request: Request) -> bool:
    """CSRF guard for the session-minting endpoint.

    Browser requests must come from the app's own origins — without this, a
    malicious page can cross-site POST a Google id_token (simple request,
    no CORS preflight) and silently log the victim in as the attacker's
    account. CLI clients (capture_dom.py, curl) send no Origin header and
    are allowed.
    """
    origin = request.headers.get("origin", "").rstrip("/")
    if not origin:
        return True
    return origin in {
        settings.frontend_url.rstrip("/"),
        settings.public_url.rstrip("/"),
    }


def _set_session_cookie(response, cookie_value: str, secure: bool) -> None:
    """Set the signed session cookie on the response."""
    response.set_cookie(
        key="session",
        value=cookie_value,
        httponly=True,
        samesite="lax",
        path="/",
        secure=secure,
        max_age=int(SESSION_MAX_AGE.total_seconds()),
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
    state = secrets.token_urlsafe(_OAUTH_STATE_BYTES)

    svc = get_services()
    try:
        authorization_url, code_verifier = svc.oauth_service.create_authorization_url(state)
    except ImportError:
        return {"status": "error", "detail": "google-auth libraries not installed"}

    if not code_verifier:
        return {"status": "error", "detail": "PKCE code_verifier not generated"}

    # Enforce a hard cap on the in-memory state store to prevent DoS
    if len(_oauth_states) >= _STATE_MAX_ENTRIES:
        _sweep_stale_states()
    if len(_oauth_states) >= _STATE_MAX_ENTRIES:
        return {"status": "error", "detail": "Too many login attempts, try again"}

    _oauth_states[state] = _OAuthState(
        code_verifier=code_verifier,
        created_at=datetime.now(UTC).timestamp(),
    )
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

    code_verifier = state_data.code_verifier
    if not code_verifier:
        return RedirectResponse(url=f"{settings.frontend_url}/?auth_error=missing_code_verifier")

    svc = get_services()
    try:
        id_info = svc.oauth_service.exchange_code(code, code_verifier, state)
        if not id_info.get("email_verified", False):
            return RedirectResponse(url=f"{settings.frontend_url}/?auth_error=email_not_verified")

        cookie_value = _build_session(id_info).cookie_value

        response = RedirectResponse(url=f"{settings.frontend_url}/")
        _set_session_cookie(response, cookie_value, _is_secure(request))
        return response

    # lucidlint: ignore broad-except OAuth callback failure redirects to the frontend with an auth_error parameter
    except Exception as e:
        logger.exception("OAuth callback failed")
        return RedirectResponse(url=f"{settings.frontend_url}/?auth_error=" + quote(str(e)))


@auth_router.post("/device")
async def device(request: Request):
    """Mint a session from a Google id_token obtained via OAuth device flow.

    Headless-friendly login: the client (e.g. tools/capture_dom.py --login)
    runs Google's device authorization grant, the human approves on any
    device, and the resulting id_token is exchanged here for the same signed
    session cookie the web callback issues. The cookie travels only in the
    Set-Cookie header (never in the body), so non-browser clients read it
    from the response cookies.
    """
    try:
        body = await request.json()
    except ValueError as e:
        logger.debug("Malformed JSON on /api/auth/device: %s", e)
        return JSONResponse(status_code=400, content={"detail": "invalid JSON body"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"detail": "JSON object required"})
    token = body.get("id_token", "")
    if not token:
        return JSONResponse(status_code=400, content={"detail": "id_token required"})

    if not _origin_allowed(request):
        logger.warning(
            "Device-flow login rejected: cross-origin request from %s",
            request.headers.get("origin", "?"),
        )
        return JSONResponse(status_code=403, content={"detail": "cross-origin requests not allowed"})

    if not settings.device_client_id:
        logger.warning(
            "Device-flow login attempted but HOUSES_GOOGLE_DEVICE_CLIENT_ID is unset — "
            "no device OAuth client configured"
        )
        return JSONResponse(status_code=503, content={"detail": "device client not configured"})

    svc = get_services()
    try:
        id_info = await svc.oauth_service.verify_id_token(token)
    except TransportError as e:
        logger.warning("Device-flow id_token verification failed (transport): %s", e)
        return JSONResponse(
            status_code=503, content={"detail": "identity provider unreachable, try again"}
        )
    # lucidlint: ignore broad-except device-flow id_token verification failure returns a 401 JSON response
    except Exception as e:
        logger.warning("Device-flow id_token verification failed: %s", e)
        return JSONResponse(status_code=401, content={"detail": "invalid id_token"})
    if not id_info.get("email_verified", False):
        return JSONResponse(status_code=401, content={"detail": "email not verified"})

    mint = _build_session(id_info)
    payload, cookie_value = mint.payload, mint.cookie_value
    response = JSONResponse(
        content={"authenticated": True, **payload},
        headers={"Cache-Control": "no-store"},
    )
    _set_session_cookie(response, cookie_value, _is_secure(request))
    return response


@auth_router.get("/me")
async def me(request: Request):
    """Return current authentication state.

    When authenticated, includes email, name, picture, person,
    is_superuser, and impersonating (if a superuser is impersonating someone).
    is_superuser is re-derived from LIVE settings, so a promotion applies
    to an existing session without re-login.
    """
    session = effective_session_user(request)
    if not session:
        return {"authenticated": False}

    # Look up associated Person by email
    person_name = _current_person_name(session)

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
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
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def impersonate(request: Request, body: dict):
    """Set or clear impersonation for a superuser.

    Body::

        { "person": "Simon" }   # start impersonating
        { "person": null }      # stop impersonating

    Returns a new session cookie with the ``impersonating`` field updated.
    Survives server restarts.
    """
    session = effective_session_user(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not session.get("is_superuser"):
        raise HTTPException(status_code=403, detail="Only superusers can impersonate")

    person = body.get("person")
    if person is not None and not isinstance(person, str):
        raise HTTPException(status_code=400, detail="person must be a string or null")
    if person is not None:
        persons_attempt = get_services().persons_source.latest_attempt()
        for p in persons_attempt.value_or_none() or []:
            name = getattr(p, "name", None) if not isinstance(p, dict) else p.get("name")
            if name == person:
                is_child = bool(p.get("is_child")) if isinstance(p, dict) else bool(getattr(p, "is_child", False))
                if is_child:
                    raise HTTPException(status_code=400, detail="Cannot impersonate a child")
                break

    new_cookie = _make_session_cookie(
        email=session["email"],
        name=session["name"],
        picture=session.get("picture", ""),
        is_superuser=session.get("is_superuser", False),
        impersonating=person,
    )

    # lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    response = JSONResponse(content={"status": "ok", "impersonating": person})
    _set_session_cookie(response, new_cookie, _is_secure(request))
    return response
