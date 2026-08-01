#!/usr/bin/env python3
"""Capture rendered DOM from the Vue frontend for comparison / debugging.

Usage:
    python tools/capture_dom.py [--output /tmp] [--list-only] [--detail-only]
    python tools/capture_dom.py --login [--state-file PATH]

Requires the backend (port 8765) and frontend (port 5173) to be running.

Authenticated pages need a one-time interactive Google sign-in:

    python tools/capture_dom.py --login

Login uses Google's OAuth device flow — no browser automation, no credentials
on this machine: the script prints a code, you approve from any device (your
browser's saved credentials + 2FA), and the session (a localhost-scoped
cookie, never credentials) is serialized to ``tools/.auth-state.json``
(Playwright ``storageState``: gitignored, never commit) and replayed into
every later capture. A missing or expired session fails loudly instead of
silently capturing the login page.

The script waits for both servers to respond before navigating.
A single browser instance is reused across captures.
"""

import asyncio
import json
import logging
import os
import sys
import time
from argparse import ArgumentParser
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import httpx

FRONTEND_URL = "http://localhost:5173"
BACKEND_URL = "http://localhost:8765"
LAUNCH_OPTS = {"headless": True, "args": ["--no-sandbox", "--disable-gpu"]}
LOGIN_TIMEOUT_S = 10 * 60
GOOGLE_DEVICE_URL = "https://oauth2.googleapis.com/device/code"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DEVICE_SCOPE = "openid email profile"
AUTH_HINT = "Session expired or invalid — run 'python tools/capture_dom.py --login'"

logger = logging.getLogger("capture_dom")


def _fail(user_message: str, dev_detail: str) -> NoReturn:
    """Fail fast with two messages (see docs/coding-standards.md, two-tier failures):
    a plain-language line for the user and a resolving detail line for the
    developer — stderr is this tool's diagnostic channel.
    """
    print(user_message, file=sys.stderr)
    logger.warning("%s", dev_detail)
    sys.exit(1)


def _session_dir() -> Path:
    """A session-scoped output directory under the project's tools/ dir.

    Files are grouped by session date so they're attributable and don't
    accumulate in /tmp forever.
    """
    base = Path(__file__).resolve().parent / "captures"
    stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")
    session = base / stamp
    session.mkdir(parents=True, exist_ok=True)
    return session


def _default_state_file() -> Path:
    """Default Playwright storageState file (gitignored, holds a live cookie)."""
    return Path(__file__).resolve().parent / ".auth-state.json"


def _storage_state(session_cookie: str) -> dict:
    """Playwright storageState carrying only the localhost session cookie."""
    return {
        "cookies": [
            {
                "name": "session",
                "value": session_cookie,
                "domain": "localhost",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }


_browser = None
_playwright = None


async def wait_for_server(url: str, label: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = await httpx.AsyncClient(timeout=3).get(url)
            if r.status_code < 500:
                return
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        await asyncio.sleep(1)
    _fail(
        f"The {label} isn't responding — start the app with `make run`, wait for it to be ready, then try again.",
        f"{label} at {url} not ready after {timeout}s",
    )


async def get_browser():
    global _browser, _playwright
    if _browser and _browser.is_connected():
        return _browser
    from playwright.async_api import async_playwright

    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(**LAUNCH_OPTS)
    return _browser


async def _auth_state(page) -> bool | None:
    """Authentication state of the page's session.

    True = authenticated, False = not authenticated, None = /api/auth/me
    unreachable (backend/proxy down) — reported as a distinct failure so a
    transient outage is never mislabelled as an expired session.
    """
    try:
        return await page.evaluate(
            "fetch('/api/auth/me')"
            ".then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)))"
            ".then(d => d.authenticated === true)"
        )
    except Exception as e:
        print(f"  WARNING: could not check session at {page.url}: {e}", file=sys.stderr)
        if not page.url.startswith(FRONTEND_URL):
            # The SPA navigated off the frontend origin (e.g. Google sign-in
            # redirect after an expired session): the failed fetch is an
            # unauthenticated page, not a backend outage.
            return False
        return None


async def login(state_file: Path) -> None:
    """Google OAuth device flow; serialize the app session to state_file.

    Prints a verification code and polls Google until the human approves from
    any device (their browser's saved credentials + 2FA do the signing — no
    credentials are entered on or stored by this machine). The resulting
    id_token is exchanged with the backend for the app's session cookie,
    saved as localhost-only Playwright storageState. Works on a headless box.
    """
    from houses.config import settings

    client_id = settings.device_client_id
    if not client_id:
        _fail(
            "Google sign-in isn't set up on this machine — run the sign-in command again once it's been configured.",
            "HOUSES_GOOGLE_DEVICE_CLIENT_ID unset: create a 'TVs and Limited Input devices' OAuth client "
            "in Google Cloud Console (no redirect URIs needed) and add its ID to .env",
        )
    # Device-flow clients may have a client secret (some console-created
    # clients issue one) or none. Send it only when configured — an empty
    # client_secret in the token request can make Google reject it with
    # invalid_client.
    client_secret = settings.device_client_secret

    print("Waiting for servers …")
    await wait_for_server(BACKEND_URL + "/api/auth/me", "backend")
    await wait_for_server(FRONTEND_URL, "frontend")
    print(f"Session state will be saved to: {state_file}")

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.post(
                GOOGLE_DEVICE_URL,
                data={"client_id": client_id, "scope": GOOGLE_DEVICE_SCOPE},
            )
        except httpx.HTTPError as e:
            _fail(
                "Couldn't reach Google's sign-in service — check your connection and try again.",
                f"device-flow setup POST to {GOOGLE_DEVICE_URL} failed: {e}",
            )
        if r.status_code != 200:
            _fail(
                "Couldn't start Google sign-in — the sign-in app isn't allowed to use device sign-in. "
                "Nothing you did wrong; try again once it's fixed.",
                f"Google device flow setup failed ({r.status_code}): {r.text[:300]} — "
                f"is device flow enabled for OAuth client {client_id}?",
            )
        try:
            info = r.json()
        except ValueError as e:
            _fail(
                "Couldn't start Google sign-in — the sign-in service sent an unexpected reply. Try again.",
                f"device-flow setup response was not JSON: {e}",
            )
        device_code = info["device_code"]
        user_code = info["user_code"]
        verification_url = info.get("verification_url", "https://www.google.com/device")
        interval = int(info.get("interval", 5))

    print(f"1) Open {verification_url} in any browser")
    print(f"2) Enter code: {user_code}")
    print("3) Approve with your Google account (2FA if asked)")
    print(f"Waiting for approval (up to {LOGIN_TIMEOUT_S // 60} min) …")

    async with httpx.AsyncClient(timeout=30) as client:
        deadline = time.monotonic() + LOGIN_TIMEOUT_S
        token_data = {
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        if client_secret:
            token_data["client_secret"] = client_secret
        while time.monotonic() < deadline:
            try:
                r = await client.post(GOOGLE_TOKEN_URL, data=token_data)
                data = r.json()
            except (httpx.HTTPError, ValueError) as e:
                # Transient blip (network, proxy 5xx HTML body): keep polling —
                # the deadline below bounds the retry; log so a silent loop of
                # failures is diagnosable.
                logger.warning("device-flow token poll failed (retrying): %s", e)
                await asyncio.sleep(interval)
                continue
            if r.status_code == 200:
                id_token = data.get("id_token")
                if not id_token:
                    _fail(
                        "Google finished the sign-in but didn't hand back a session — the sign-in app is "
                        "missing a permission. Tell the developer, then try again.",
                        f"Google returned no id_token on device flow — is the 'openid email profile' scope "
                        f"enabled for client {client_id}?",
                    )
                break
            err = data.get("error")
            if err == "authorization_pending":
                pass
            elif err == "slow_down":
                interval += 5
            elif err in ("access_denied", "expired_token"):
                _fail(
                    f"Google sign-in was {err.replace('_', ' ')} — nothing was signed in. "
                    "Try again if that was a mistake.",
                    f"Google device flow terminated: {err}",
                )
            else:
                desc = data.get("error_description")
                _fail(
                    "Google sign-in failed — nothing was signed in. Try again.",
                    f"Google device flow error: {err} {desc or r.text[:200]}".strip(),
                )
            await asyncio.sleep(interval)
        else:
            _fail(
                f"No one approved the sign-in within {LOGIN_TIMEOUT_S // 60} minutes — nothing was signed in. "
                f"Run the command again when you can approve it.",
                f"no device-flow approval after {LOGIN_TIMEOUT_S // 60} min",
            )

        # Exchange the id_token for the app's signed session cookie
        try:
            r = await client.post(BACKEND_URL + "/api/auth/device", json={"id_token": id_token})
        except httpx.HTTPError as e:
            _fail(
                "Couldn't reach the app to finish signing in — check that it's running, then try again.",
                f"device login POST to {BACKEND_URL}/api/auth/device failed: {e}",
            )
        if r.status_code != 200:
            if r.status_code == 503:
                _fail(
                    "Couldn't complete the sign-in — the sign-in service was unreachable. "
                    "Check your connection and try again.",
                    f"Backend device login returned 503 (identity provider unreachable): {r.text[:300]}",
                )
            _fail(
                "The app refused the sign-in — check that the app is running and up to date, then try again.",
                f"Backend rejected device login ({r.status_code}): {r.text[:300]}",
            )
        session_cookie = r.cookies.get("session")
        if not session_cookie:
            _fail(
                "Sign-in completed but the app didn't create a session — try again.",
                "Backend set no session cookie on POST /api/auth/device",
            )

    state_file.parent.mkdir(parents=True, exist_ok=True)
    # Create owner-only from the start: write_text() would briefly create the
    # file with the default umask before chmod runs. 0o600 is still masked by
    # umask, so this can only be more restrictive, never less.
    fd = os.open(state_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(_storage_state(session_cookie), f, indent=2)
    print(f"Session saved → {state_file} (localhost session cookie only)")


async def capture_page(url: str, output_dir: str | Path, label: str, state_file: Path):
    browser = await get_browser()
    context = await browser.new_context(
        viewport={"width": 1280, "height": 2048},
        ignore_https_errors=True,
        storage_state=str(state_file),
    )
    page = await context.new_page()

    errors = []

    def on_console(msg):
        if msg.type == "error":
            errors.append(msg.text[:300])

    page.on("console", on_console)
    page.on("pageerror", lambda e: errors.append(f"PAGE: {str(e)[:300]}"))

    await page.goto(url, wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(5000)

    auth = await _auth_state(page)
    if auth is None:
        await context.close()
        _fail(
            "The app's server isn't responding — fix that, then re-run this capture.",
            f"{page.url} — backend unreachable during session check",
        )
    if not auth:
        await context.close()
        _fail(
            f"{AUTH_HINT}, then re-run this capture.",
            f"{page.url} — no authenticated session (session cookie missing or expired)",
        )

    html = await page.content()
    html_path = Path(output_dir) / f"dom_{label}.html"
    html_path.write_text(html)
    print(f"  DOM saved ({len(html)} bytes) → {html_path}")

    screenshot_path = Path(output_dir) / f"screenshot_{label}.png"
    await page.screenshot(path=str(screenshot_path), full_page=True)
    print(f"  Screenshot → {screenshot_path}")

    if errors:
        print(f"  Console errors ({len(errors)}):")
        for e in errors[:5]:
            print(f"    {e}")

    cards = await page.query_selector_all(".card")
    print(f"  Cards: {len(cards)}")
    for i, card in enumerate(cards[:2]):
        text = (await card.text_content()).strip()[:500]
        print(f"\n  --- Card {i} ---")
        print(f"  {text}")

    await context.close()


async def main():
    parser = ArgumentParser(description="Capture Vue frontend DOM (authenticated pages)")
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output directory (default: tools/captures/<session-timestamp>)",
    )
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--detail-only", action="store_true")
    parser.add_argument(
        "--login",
        action="store_true",
        help="Interactive one-time Google sign-in; saves session state and exits.",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help=f"Playwright storageState file (default: {_default_state_file()})",
    )
    args = parser.parse_args()

    state_file = Path(args.state_file) if args.state_file else _default_state_file()

    if args.login:
        await login(state_file)
        return

    if not state_file.exists():
        _fail(
            "You're not signed in — run 'python tools/capture_dom.py --login' once to sign in, "
            "then re-run this capture.",
            f"No auth state at {state_file} — session cookie file missing or deleted",
        )

    out = Path(args.output) if args.output else _session_dir()
    out.mkdir(parents=True, exist_ok=True)

    print("Waiting for servers …")
    await wait_for_server(BACKEND_URL + "/api/properties", "backend")
    await wait_for_server(FRONTEND_URL, "frontend")

    urls = []
    if not args.detail_only:
        urls.append((FRONTEND_URL + "/", "list"))
    if not args.list_only:
        urls.append((FRONTEND_URL + "/#/property/89306649", "detail"))

    for url, label in urls:
        print(f"\nCapturing {label} page …")
        await capture_page(url, out, label, state_file)

    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()


if __name__ == "__main__":
    asyncio.run(main())
