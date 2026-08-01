"""Hermetic tests for tools/capture_dom.py auth handling (no servers, no browser).

The interactive login flow needs a real Google 2FA prompt and is not testable
here; these cover the fail-loud contract (authenticated pages must never be
silently captured without a session) and the credential-safety helpers
(localhost-only storage state, Chrome profile copy).
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "capture_dom.py"


def _load_capture_dom():
    spec = importlib.util.spec_from_file_location("capture_dom", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_help_lists_auth_flags():
    result = _run("--help")
    assert result.returncode == 0
    for flag in ("--login", "--state-file"):
        assert flag in result.stdout


def test_missing_state_file_fails_with_login_hint(tmp_path):
    result = _run("--state-file", str(tmp_path / "missing.json"))
    assert result.returncode != 0
    assert "--login" in result.stderr
    assert "auth state" in result.stderr


def test_storage_state_carries_only_localhost_session_cookie():
    cd = _load_capture_dom()

    state = cd._storage_state("cookie-value")
    assert state["cookies"] == [
        {
            "name": "session",
            "value": "cookie-value",
            "domain": "localhost",
            "path": "/",
            "expires": -1,
            "httpOnly": True,
            "secure": False,
            "sameSite": "Lax",
        }
    ]
    assert state["origins"] == []


@pytest.mark.asyncio
async def test_auth_state_distinguishes_unreachable_from_unauthenticated():
    cd = _load_capture_dom()

    class FakePage:
        def __init__(self, result, url="http://localhost:5173/#/"):
            self._result = result
            self._url = url

        @property
        def url(self):
            return self._url

        async def evaluate(self, *args):
            if isinstance(self._result, Exception):
                raise self._result
            return self._result

    assert await cd._auth_state(FakePage(True)) is True
    assert await cd._auth_state(FakePage(False)) is False
    assert await cd._auth_state(FakePage(ConnectionError("backend down"))) is None
    # SPA redirected off the frontend (Google sign-in): expired session,
    # not a backend outage
    assert (
        await cd._auth_state(
            FakePage(ConnectionError("fetch failed"), url="https://accounts.google.com/o/oauth2/auth")
        )
        is False
    )
