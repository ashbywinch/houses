"""Town service error-channel tests.

Per the error-handling convention: services that call APIs return
Attempt so the failure reason survives; services that don't call APIs
throw. The town lookup calls ORS Pelias, so it returns Attempt[str]
with the reason preserved.
"""
from __future__ import annotations

import httpx
import pytest

from houses.apis.ors import SETTLEMENT_LAYERS
from houses.location import ReverseGeocodeOptions, find_nearest_town_name
from houses.town_desc import generate_town_description


class TestFindNearestTownName:
    @pytest.mark.asyncio
    async def test_returns_town_name(self):
        class _FakeCM:
            async def __aenter__(self):
                return _FakeClient()

            async def __aexit__(self, *a):
                return False

        class _FakeClient:
            async def get(self, url, params=None, headers=None):
                return _FakeResp()

        class _FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"features": [{"properties": {"locality": "Southall"}}]}

        result = await find_nearest_town_name(
            51.5,
            -0.1,
            options=ReverseGeocodeOptions(
                api_key="key",
                get_cached_fn=lambda *a, **k: None,
                set_cached_fn=lambda *a, **k: None,
                client_factory=lambda **k: _FakeCM(),
            ),
        )

        assert result.succeeded
        assert result.value_or_none() == "Southall"

    @pytest.mark.asyncio
    async def test_no_features_returns_impossible(self):
        class _FakeCM:
            async def __aenter__(self):
                return _FakeClient()

            async def __aexit__(self, *a):
                return False

        class _FakeClient:
            async def get(self, url, params=None, headers=None):
                return _FakeResp()

        class _FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"features": []}

        result = await find_nearest_town_name(
            51.5,
            -0.1,
            options=ReverseGeocodeOptions(
                api_key="key",
                get_cached_fn=lambda *a, **k: None,
                set_cached_fn=lambda *a, **k: None,
                client_factory=lambda **k: _FakeCM(),
            ),
        )

        assert result.impossible
        assert "no town found" in result.error

    @pytest.mark.asyncio
    async def test_re_raises_transient_http_error(self):
        import httpx

        class _FakeCM:
            async def __aenter__(self):
                return _FakeClient()

            async def __aexit__(self, *a):
                return False

        class _FakeClient:
            async def get(self, url, params=None, headers=None):
                raise httpx.TimeoutException("timed out")

        with pytest.raises(httpx.TimeoutException):
            await find_nearest_town_name(
                51.5,
                -0.1,
                options=ReverseGeocodeOptions(
                    api_key="key",
                    get_cached_fn=lambda *a, **k: None,
                    client_factory=lambda **k: _FakeCM(),
                ),
            )



    @pytest.mark.asyncio
    async def test_asks_for_settlements_not_streets(self):
        """The same unconstrained-reverse trap as the walkability fallback:
        without layers, Pelias names the *street* the house is on (or a
        school) as the "nearest town"."""
        seen: dict = {}

        class _FakeCM:
            async def __aenter__(self):
                return _FakeClient()

            async def __aexit__(self, *a):
                return False

        class _FakeClient:
            async def get(self, url, params=None, headers=None):
                seen.update(params or {})
                return _FakeResp()

        class _FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"features": [{"properties": {"locality": "Southall"}}]}

        await find_nearest_town_name(
            51.5,
            -0.1,
            options=ReverseGeocodeOptions(
                api_key="key",
                get_cached_fn=lambda *a, **k: None,
                set_cached_fn=lambda *a, **k: None,
                client_factory=lambda **k: _FakeCM(),
            ),
        )

        assert seen.get("layers") == SETTLEMENT_LAYERS
        assert "street" not in str(seen.get("layers"))
class TestGenerateTownDescription:
    @pytest.mark.asyncio
    async def test_returns_description(self):
        from houses.town_desc import _reset

        _reset()

        class _FakeCM:
            async def __aenter__(self):
                return _FakeClient()

            async def __aexit__(self, *a):
                return False

        class _FakeClient:
            async def post(self, url, json=None, headers=None):
                return _FakeResp()

        class _FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "A leafy suburb."}}]}

        async def _fake_cache(*a, **k):
            return _FakeResp().json()

        result = await generate_town_description(
            "Southall",
            "UB2 4GN",
            client_factory=lambda **k: _FakeCM(),
            with_cache_fn=_fake_cache,
        )

        assert result.succeeded
        assert result.value_or_none() == "A leafy suburb."

    @pytest.mark.asyncio
    async def test_asks_for_no_reasoning_on_the_intended_model(self):
        """The intended model reasons by default and spends the whole token
        budget thinking (finish_reason=length, content=None) — the request
        must switch reasoning off, or every description fails."""
        from houses.town_desc import _reset

        _reset()
        sent: dict = {}

        class _FakeCM:
            async def __aenter__(self):
                return _FakeClient()

            async def __aexit__(self, *a):
                return False
        class _FakeClient:
            async def post(self, url, json=None, headers=None):
                sent.update(json or {})
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "A leafy suburb."}}]},
                    request=httpx.Request("POST", url),
                )

        async def _fake_cache(method, url, *, body=None, fetch=None, **k):
            assert fetch is not None
            return await fetch()

        await generate_town_description(
            "Southall",
            "UB2 4GN",
            client_factory=lambda **k: _FakeCM(),
            with_cache_fn=_fake_cache,
        )

        assert sent.get("reasoning") == {"enabled": False}
        assert sent.get("model") == "deepseek/deepseek-v4.1-flash"

    @pytest.mark.asyncio
    async def test_no_content_is_an_impossible_attempt_not_a_crash(self):
        """A reasoning model that ignored the switch returns content=None;
        that is a failure with a readable reason, not an AttributeError."""
        from houses.town_desc import _reset

        _reset()

        class _FakeCM:
            async def __aenter__(self):
                return _FakeClient()

            async def __aexit__(self, *a):
                return False
        class _FakeClient:
            async def post(self, url, json=None, headers=None):
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": None}, "finish_reason": "length"}]
                    },
                    request=httpx.Request("POST", url),
                )

        async def _fake_cache(method, url, *, body=None, fetch=None, **k):
            assert fetch is not None
            return await fetch()

        result = await generate_town_description(
            "Southall",
            "UB2 4GN",
            client_factory=lambda **k: _FakeCM(),
            with_cache_fn=_fake_cache,
        )

        assert not result.succeeded
        assert "no content" in result.error
        assert "length" in result.error
