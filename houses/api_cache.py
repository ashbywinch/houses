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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, TypeVar, override
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from houses.settings import settings
from serialization import SerializableAsDict

logger = logging.getLogger(__name__)

_APP_KEY_RE = re.compile(r"app_key=[^&\"'}\s]+")

_ClientT = TypeVar("_ClientT", httpx.AsyncClient, httpx.Client)
type _WireFragment = dict[str, Any]


class _CacheRuntime:
    """The disk cache's process-wide state: where the cache lives, the
    lazy TfL key value for scrubbing, and the transient HTTP verdicts —
    one owner; tests swap the directory whole via ``set_cache_dir``."""

    CACHE_DIR: ClassVar[Path] = Path("data/api_cache")
    TFL_KEY_VALUE: ClassVar[str] = ""

    # Statuses that must never poison the cache — retries stay genuine
    # (key expiry 401/403, planner outage 409, rate limit 429, 5xx).
    TRANSIENT_STATUSES: ClassVar[tuple[int, ...]] = (401, 403, 409, 429)
    HTTP_5XX_START = 500
    HTTP_OK = 200
    HTTP_3XX_START = 300
    HTTP_NOT_FOUND = 404
    HTTP_3XX_END = 400

# lucidlint: ignore unused-setter the cache dir is the test seam — conftest swaps it; production uses the default
def set_cache_dir(path: str | Path) -> None:
    """Override the cache directory (used by tests to isolate caches)."""
    _CacheRuntime.CACHE_DIR = Path(path)


@dataclass(frozen=True)
class CacheEnvelope:
    """The wrapped-error envelope stored in the cache file: a
    deterministic non-2xx response with its status preserved."""

    status: int
    body: object

    def to_dict(self) -> dict:
        return dict(_cached_status=self.status, _cached_body=self.body)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CacheEnvelope:
        """The cache-file envelope, read at the cache edge."""
        return cls(status=data["_cached_status"], body=data["_cached_body"])


def _wire_params(params: SerializableAsDict | Mapping[str, Any] | None) -> _WireFragment | None:
    """Serialise a request record exactly once, at the cache-key edge.

    The cache seams take the caller's request RECORD (``SerializableAsDict``) or a
    plain wire dict: records serialize via ``to_dict()`` here, never in the
    callers. Plain dicts are the wire already; falsy values are dropped to
    mirror httpx (the transport omits them from the query, so the cache
    identity must match what is actually on the wire).
    """
    if params is None:
        return None
    if isinstance(params, Mapping):
        wire: _WireFragment = {k: v for k, v in params.items() if v}
    else:
        wire = params.to_dict()
    # Normalize an empty serialization to None EXPLICITLY: the key omits
    # the query part when there is nothing on the wire, and no later truthy
    # check should be what makes that true.
    return wire or None

# lucidlint: ignore latent-class probed: 3+ fns sharing 2 params; CacheRequest re-fires first-arg; API is cross-module
def _make_key(method: str, url: str, params: SerializableAsDict | Mapping[str, Any] | None, body: str | None) -> str:
    parts = [method.upper(), url]
    wire = _wire_params(params)
    if wire:
        parts.append(json.dumps(wire, sort_keys=True))
    if body:
        parts.append(body)
    raw = "::".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _cache_path(key: str) -> Path:
    return _CacheRuntime.CACHE_DIR / f"{key}.json"

def get_cached(
    method: str,
    url: str,
    params: SerializableAsDict | Mapping[str, Any] | None = None,
    body: str | None = None,
) -> Mapping[str, Any] | None:
    """Return the cached JSON response for a request, or ``None``.

    ``params`` is the caller's request record — serialized once at the
    cache-key edge.
    """
    path = _cache_path(_make_key(method, url, params, body))
    if path.exists():
        return json.loads(path.read_text())
    return None

def set_cached(
    method: str,
    url: str,
    params: SerializableAsDict | Mapping[str, Any] | None,
    body: str | None,
    data: Mapping[str, Any],
) -> None:
    """Store a JSON response so future identical requests skip the API."""
    _CacheRuntime.CACHE_DIR.mkdir(parents=True, exist_ok=True)
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


def _cached_secret_key() -> str:
    """The TfL app key VALUE (lazily read) — scrubbing the raw value
    catches escaped/JSON-encoded echoes the query regex misses."""
    if not _CacheRuntime.TFL_KEY_VALUE:
        _CacheRuntime.TFL_KEY_VALUE = settings.tfl_api_key
    return _CacheRuntime.TFL_KEY_VALUE

def evict_cached(
    method: str, url: str, params: SerializableAsDict | Mapping[str, Any] | None, body: str | None
) -> None:
    """Delete a cached response (e.g. a poisoned error body). No-op if absent."""
    _cache_path(_make_key(method, url, params, body)).unlink(missing_ok=True)


# lucidlint: ignore long-param-list the flat signature is this helper's ease-of-use contract (cache callers)
async def with_cache(
    method: str,
    url: str,
    params: SerializableAsDict | Mapping[str, Any] | None = None,
    body: SerializableAsDict | Mapping[str, Any] | None = None,
    *,
    fetch,
    no_store: bool = False,
) -> Mapping[str, Any]:
    """Check disk cache first; on miss call ``fetch``, cache result, return.

    ``fetch`` is an async callable that returns the parsed JSON dict.
    Example::

        data = await with_cache("GET", url, params=params, fetch=lambda: resp.json())

    ``params``/``body`` are the caller's request records — serialized once
    at the cache-key edge; legacy plain dicts pass through unchanged.
    """
    body_str = json.dumps(_wire_params(body), sort_keys=True) if body else None
    if no_store:
        # test-injection seam (``_no_cache``), the DI sibling of a client
        # factory: call the fetch without reading or writing the disk cache
        return await fetch()
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
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()} if parsed.query else None
        if query:
            query = {k: v for k, v in query.items() if k != "app_key"} or None
        # The URL query IS the wire request at this edge — a plain mapping,
        # never a record that only unwraps itself back into one.
        params = query
        body = request.content.decode() if request.content else None

        cached = get_cached(request.method, url_path, params, body)
        if cached is not None:
            status = cached.get("_cached_status") if isinstance(cached, dict) else None
            raw_status = cached.get("httpStatusCode") if isinstance(cached, dict) else None
            poison = status if isinstance(status, int) else raw_status if isinstance(raw_status, int) else None
            if poison is not None and (
                poison in _CacheRuntime.TRANSIENT_STATUSES or poison >= _CacheRuntime.HTTP_5XX_START
            ):
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
                return httpx.Response(_CacheRuntime.HTTP_OK, json=cached)

        response = await self._inner.handle_async_request(request)
        # Cache only deterministic responses: 2xx raw; 3xx and 404 wrapped with
        # their status. 401/403 (key expiry), 409 (planner outage), 429 and 5xx
        # are transient and must never poison the cache.
        try:
            data = response.json()
            if response.is_success:
                set_cached(request.method, url_path, params, body, data)
            elif (
                _CacheRuntime.HTTP_3XX_START <= response.status_code < _CacheRuntime.HTTP_3XX_END
                or response.status_code == _CacheRuntime.HTTP_NOT_FOUND
            ):
                set_cached(
                    request.method,
                    url_path,
                    params,
                    body,
                    CacheEnvelope(status=response.status_code, body=data).to_dict(),
                )
        # A cache-write failure must never break the request.
        except (OSError, TypeError, ValueError) as e:
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
