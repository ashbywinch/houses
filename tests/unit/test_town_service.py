"""Town service error-channel tests.

Per the error-handling convention: services that call APIs return
Attempt so the failure reason survives; services that don't call APIs
throw. The town lookup calls ORS Pelias, so it returns Attempt[str]
with the reason preserved.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from dag.attempt import Attempt
from houses.location import find_nearest_town_name
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

        with (
            patch("houses.location.get_cached", return_value=None),
            patch("houses.location.cached_async_client", return_value=_FakeCM()),
            patch("houses.location.settings.ors_api_key", "key"),
            patch("houses.location.set_cached"),
        ):
            result = await find_nearest_town_name(51.5, -0.1)

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

        with (
            patch("houses.location.get_cached", return_value=None),
            patch("houses.location.cached_async_client", return_value=_FakeCM()),
            patch("houses.location.settings.ors_api_key", "key"),
            patch("houses.location.set_cached"),
        ):
            result = await find_nearest_town_name(51.5, -0.1)

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

        with (
            patch("houses.location.get_cached", return_value=None),
            patch("houses.location.cached_async_client", return_value=_FakeCM()),
            patch("houses.location.settings.ors_api_key", "key"),
        ):
            with pytest.raises(httpx.TimeoutException):
                await find_nearest_town_name(51.5, -0.1)


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

        with (
            patch("houses.town_desc.cached_async_client", return_value=_FakeCM()),
            patch("houses.town_desc.with_cache", new=_fake_cache),
        ):
            result = await generate_town_description("Southall", "UB2 4GN")

        assert result.succeeded
        assert result.value_or_none() == "A leafy suburb."
