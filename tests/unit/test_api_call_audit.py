"""The outbound-call audit log: what was called, by whom, and what came back.

The 2026-10-03 ORS incident was unattributable because the gateway threw away
the response body and never said which code path made the call: a 403 carrying
"Quota exceeded" and one carrying "Invalid key" were the same silence. These
tests pin the three facts that fix that, plus the rule that no secret may reach
the log.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import httpx
import pytest

from houses import apigw


class _Client:
    """httpx-shaped fake: returns, or raises, exactly what the test sets."""

    def __init__(self, *, status: int = 200, text: str = "{}", raises: bool = False) -> None:
        self.status = status
        self.text = text
        self.raises = raises
        self.calls: list[str] = []

    async def __aenter__(self) -> _Client:
        return self

    @staticmethod
    async def __aexit__(*exc: object) -> bool:
        return False

    async def request(self, method: str, url: str, *args: object, **kwargs: object) -> httpx.Response:
        self.calls.append(url)
        request = httpx.Request(method, url)
        response = httpx.Response(self.status, text=self.text, request=request)
        if self.raises:
            raise httpx.HTTPStatusError(f"HTTP {self.status} for {url}", request=request, response=response)
        return response


def _args(client: _Client, *, url: str = "https://api.example/directions?api_key=SECRET123") -> apigw.FetchArgs:
    return apigw.FetchArgs("GET", url, _client_factory=lambda **kw: client, no_cache=True)


@pytest.fixture(autouse=True)
def _clean_gate() -> Iterator[None]:
    apigw.GATE.clear_quota()
    apigw.GATE.reset_pacing()
    yield
    apigw.GATE.clear_quota()
    apigw.GATE.reset_pacing()


def _lines(caplog: pytest.LogCaptureFixture, prefix: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefix)]


async def test_a_real_call_logs_the_method_the_app_frame_and_a_masked_url(caplog):
    client = _Client(status=200, text='{"ok": true}')
    with caplog.at_level(logging.INFO, logger=apigw.logger.name):
        await apigw.api_fetch(_args(client), api=apigw.ORS)

    calls = _lines(caplog, "api call ")
    assert len(calls) == 1, f"expected exactly one call line, got {calls}"
    line = calls[0]
    assert "GET https://api.example/directions?api_key=***" in line
    assert "SECRET123" not in line, "the API key reached the log"
    # Attribution: the chain names app frames. Called from a test, the only app
    # frame on the stack is the cache layer that invokes the fetch.
    assert "api_cache.py" in line, f"the line names no app frame: {line}"


async def test_a_quota_response_logs_and_raises_with_its_body(caplog):
    client = _Client(status=403, text='{"error": "Quota exceeded"}')
    with (
        caplog.at_level(logging.WARNING, logger=apigw.logger.name),
        pytest.raises(apigw.DailyQuotaError) as caught,
    ):
        await apigw.api_fetch(_args(client), api=apigw.ORS)

    assert "Quota exceeded" in str(caught.value), "the body is what makes this diagnosable"
    assert any("Quota exceeded" in line for line in _lines(caplog, "api response ")), (
        "the body must reach the log"
    )
    assert apigw.GATE.quota_exhausted(apigw.ORS), "a quota status still flips the flag"


async def test_a_raised_quota_error_keeps_its_body_and_redacts_the_url():
    client = _Client(status=403, text='{"error": "Invalid key"}', raises=True)
    with pytest.raises(apigw.DailyQuotaError) as caught:
        await apigw.api_fetch(_args(client), api=apigw.ORS)

    message = str(caught.value)
    assert "Invalid key" in message, "a raised status must carry the body too — that was the blind spot"
    assert "SECRET123" not in message, "the raised message must not leak the key"


async def test_a_body_that_echoes_a_key_is_redacted_in_the_log(caplog):
    client = _Client(status=403, text='{"error": "bad", "echo": "api_key=SECRET123"}')
    with (
        caplog.at_level(logging.WARNING, logger=apigw.logger.name),
        pytest.raises(apigw.DailyQuotaError),
    ):
        await apigw.api_fetch(_args(client), api=apigw.ORS)

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "SECRET123" not in logged, "a key reflected in a response body must not be logged"
    assert "api_key=***" in logged


def test_the_caller_chain_ignores_frames_outside_the_app():
    """The chain must name app code, never the venv's own pytest frames."""
    chain = apigw._caller_chain()
    assert "_pytest" not in chain
    assert ".venv" not in chain
