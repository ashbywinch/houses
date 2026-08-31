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
import logging
import re
from pathlib import Path
from typing import Any, TypeVar, override
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from houses.settings import settings

logger = logging.getLogger(__name__)

_APP_KEY_RE = re.compile(r"app_key=[^&\"'}\s]+")

_ClientT = TypeVar("_ClientT", httpx.AsyncClient, httpx.Client)


CACHE_DIR = Path("data/api_cache")

# lucidlint: ignore unused,unused-setter test-injection API — unit conftest calls set_cache_dir() to isolate the cache
def set_cache_dir(path: str | Path) -> None:
    """Override the cache directory (used by tests to isolate caches)."""
    # lucidlint: ignore global-state deliberate test seam — unit conftest calls set_cache_dir() to isolate the cache
    global CACHE_DIR
    CACHE_DIR = Path(path)

# lucidlint: ignore-file data-clump this module's public cache API deliberately threads one request identity (method,
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
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


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def get_cached(
    method: str,
    url: str,
    params: dict[str, Any] | None = None,
    body: str | None = None,
) -> dict[str, Any] | None:
    """Return the cached JSON response for a request, or ``None``."""
    path = _cache_path(_make_key(method, url, params, body))
    if path.exists():
        return json.loads(path.read_text())
    return None


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def set_cached(method: str, url: str, params: dict[str, Any] | None, body: str | None, data: dict[str, Any]) -> None:
    """Store a JSON response so future identical requests skip the API."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(_make_key(method, url, params, body))
    path.write_text(json.dumps(_scrub_secrets(data)))


def _scrub_secrets(obj: Any, secret_key_value: str | None = None) -> Any:
    """Strip API keys echoed inside cached response bodies.

    Cache KEYS already exclude auth params; response bodies are another
    matter — TfL error payloads echo the request URL, including
    ``app_key``.  Scrub any ``app_key=...`` AND any occurrence of the
    raw key value (escaped/JSON-encoded forms slip past the query
    regex) in string values, recursively.

    ``secret_key_value`` is a test seam (DI): pass the raw key explicitly
    instead of the lazy settings lookup.
    """
    if secret_key_value is None:
        secret_key_value = _cached_secret_key()
    if isinstance(obj, dict):
        return {k: _scrub_secrets(v, secret_key_value) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_secrets(item, secret_key_value) for item in obj]
    if isinstance(obj, str):
        scrubbed = obj
        if "app_key=" in scrubbed:
            scrubbed = _APP_KEY_RE.sub("app_key=REDACTED", scrubbed)
        if secret_key_value and secret_key_value in scrubbed:
            scrubbed = scrubbed.replace(secret_key_value, "REDACTED")
        return scrubbed
    return obj


_cached_secret_key_value: str = ""


def _cached_secret_key() -> str:
    """The TfL app key VALUE (lazily read) — scrubbing the raw value
    catches escaped/JSON-encoded echoes the query regex misses."""
    # lucidlint: ignore global-state lazy memo of the immutable settings value; single writer
    global _cached_secret_key_value
    if not _cached_secret_key_value:
        _cached_secret_key_value = settings.tfl_api_key
    return _cached_secret_key_value


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def evict_cached(method: str, url: str, params: dict[str, Any] | None, body: str | None) -> None:
    """Delete a cached response (e.g. a poisoned error body). No-op if absent."""
    _cache_path(_make_key(method, url, params, body)).unlink(missing_ok=True)


# lucidlint: ignore record-shape request params enter the cache key verbatim — wire format (coding-standards.md)
# lucidlint: ignore record-shape request body is the external API's wire payload (coding-standards.md)
async def with_cache(  # lucidlint: ignore record-shape return is the cached API response body — wire format
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
        self._inner: httpx.AsyncBaseTransport = inner or httpx.AsyncHTTPTransport()

    @override
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
                    # lucidlint: ignore record-shape wire-format dict — the wrapped-error envelope IS the cache file's
                    body,
                    {"_cached_status": response.status_code, "_cached_body": data},
                )
        # lucidlint: ignore broad-except deliberate fallback — a cache-write failure must never break the request
        except Exception as e:
            logger.debug("response not cached (%s): %s", url_path, e)
            return response
        return response


def _cached_client(client_factory: type[_ClientT], **kwargs) -> _ClientT:
    """Build an HTTP client whose transport caches every response to disk."""
    kwargs.setdefault("transport", CachingTransport())
    return client_factory(**kwargs)


def cached_async_client(**kwargs) -> httpx.AsyncClient:
    """Return an ``AsyncClient`` that auto-caches every response to disk."""
    return _cached_client(httpx.AsyncClient, **kwargs)


def cached_sync_client(**kwargs) -> httpx.Client:
    """Return a ``Client`` that auto-caches every response to disk."""
    return _cached_client(httpx.Client, **kwargs)
