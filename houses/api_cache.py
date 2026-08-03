"""Disk-backed cache for external API responses.

Cache entries are keyed by ``(method, url, params, body)`` so identical
requests return the cached response instead of re-hitting the API. The
cache is persistent across server restarts.

Usage in enrichment functions::

    from houses.api_cache import get_cached, set_cached

    key = ("GET", url, params, None)
    cached = get_cached(*key)
    if cached:
        return cached
    ...
    set_cached(*key, resp.json())
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

CACHE_DIR = Path("data/api_cache")


def set_cache_dir(path: str | Path) -> None:
    """Override the cache directory (used by tests to isolate caches)."""
    global CACHE_DIR  # type: ignore[global-statement]
    CACHE_DIR = Path(path)


def _make_key(method: str, url: str, params: dict[str, Any] | None, body: str | None) -> str:
    parts = [method.upper(), url]
    if params:
        parts.append(json.dumps(params, sort_keys=True))
    if body:
        parts.append(body)
    raw = "::".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def get_cached(
    method: str,
    url: str,
    params: dict[str, Any] | None = None,
    body: str | None = None,
) -> dict[str, Any] | None:
    """Return the cached JSON response for a request, or ``None``."""
    path = _cache_path(_make_key(method, url, params, body))
    if path.exists():
        return json.loads(path.read_text())  # type: ignore[no-any-return]
    return None


def set_cached(method: str, url: str, params: dict[str, Any] | None, body: str | None, data: dict[str, Any]) -> None:
    """Store a JSON response so future identical requests skip the API."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(_make_key(method, url, params, body))
    path.write_text(json.dumps(data))


def evict_cached(method: str, url: str, params: dict[str, Any] | None, body: str | None) -> None:
    """Delete a cached response (e.g. a poisoned error body). No-op if absent."""
    _cache_path(_make_key(method, url, params, body)).unlink(missing_ok=True)


def with_cache_sync(
    method: str,
    url: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    *,
    fetch,
) -> dict[str, Any]:
    """Sync version of ``with_cache`` — for use with ``httpx.Client``."""
    body_str = json.dumps(body, sort_keys=True) if body else None
    cached = get_cached(method, url, params, body_str)
    if cached is not None:
        return cached
    data = fetch()
    set_cached(method, url, params, body_str, data)
    return data


async def with_cache(
    method: str,
    url: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    *,
    fetch,
) -> dict[str, Any]:
    """Check disk cache first; on miss call ``fetch``, cache result, return.

    ``fetch`` is an async callable that returns the parsed JSON dict.
    Example::

        data = await with_cache("GET", url, params=params, fetch=lambda: resp.json())
    """
    body_str = json.dumps(body, sort_keys=True) if body else None
    cached = get_cached(method, url, params, body_str)
    if cached is not None:
        return cached
    data = await fetch()
    set_cached(method, url, params, body_str, data)
    return data


class CachingTransport(httpx.AsyncBaseTransport):
    """httpx async transport that checks the disk cache before making HTTP calls.

    On a cache hit the stored JSON is returned with its original HTTP status.
    On a miss the request is forwarded to ``_inner`` and deterministic
    responses (2xx/3xx/4xx, including 404 no-route bodies) are cached so
    re-processing doesn't hit APIs; TRANSIENT errors (429/5xx) are never
    cached — retries stay genuine and a cached outage body can't poison a route.

    Stores raw response bodies (not wrapped) so that enrichment functions
    using ``get_cached`` directly remain compatible.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None):
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        # Key space is UNIFIED with the callers' direct get_cached/set_cached
        # calls: the decoded URL (httpx percent-encodes path spaces) with
        # app_key stripped. Before this, the transport keyed the encoded URL
        # with auth params — a parallel key space where every response was
        # stored twice and each layer was blind to the other's entries.
        url_path = unquote(f"{parsed.scheme}://{parsed.netloc}{parsed.path}")
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()} if parsed.query else None
        if params:
            params = {k: v for k, v in params.items() if k != "app_key"} or None
        body = request.content.decode() if request.content else None

        cached = get_cached(request.method, url_path, params, body)
        if cached is not None:
            status = cached.get("_cached_status") if isinstance(cached, dict) else None
            raw_status = cached.get("httpStatusCode") if isinstance(cached, dict) else None
            poison = status if isinstance(status, int) else raw_status if isinstance(raw_status, int) else None
            if poison is not None and (poison in (401, 403, 409, 429) or poison >= 500):
                # Legacy poisoned entry: auth failures, planner outages, rate
                # limits and server errors cached before the whitelist rule.
                # Evict so retries stay genuine.
                evict_cached(request.method, url_path, params, body)
                cached = None
            elif isinstance(cached, dict) and "_cached_status" in cached:
                # Wrapped deterministic non-2xx — serve with its REAL status
                # (a cached 404 "no route" must not come back as HTTP 200).
                return httpx.Response(cached["_cached_status"], json=cached["_cached_body"])
            else:
                return httpx.Response(200, json=cached)

        response = await self._inner.handle_async_request(request)
        # Cache only deterministic responses: 2xx raw; 3xx and 404 wrapped with
        # their status. 401/403 (key expiry), 409 (planner outage), 429 and 5xx
        # are transient and must never poison the cache.
        try:
            data = response.json()
            if response.is_success:
                set_cached(request.method, url_path, params, body, data)
            elif 300 <= response.status_code < 400 or response.status_code == 404:
                set_cached(
                    request.method,
                    url_path,
                    params,
                    body,
                    {"_cached_status": response.status_code, "_cached_body": data},
                )
        except Exception:
            pass
        return response


def cached_async_client(**kwargs) -> httpx.AsyncClient:
    """Return an ``AsyncClient`` that auto-caches every response to disk."""
    kwargs.setdefault("transport", CachingTransport())
    return httpx.AsyncClient(**kwargs)


def cached_sync_client(**kwargs) -> httpx.Client:
    """Return a ``Client`` that auto-caches every response to disk."""
    kwargs.setdefault("transport", CachingTransport())
    return httpx.Client(**kwargs)
