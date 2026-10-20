"""Scrape property details from Rightmove pages.

Two modes:
  **Development** — When ``settings.rightmove_sample_page`` is set, always
    read from that local HTML file. Never hits Rightmove live.

  **Production** — Starts or connects to a Chrome instance with remote
    debugging enabled, navigates to the property page, caches the HTML by
    Rightmove ID so the same page is never fetched twice, and applies
    randomised back-off before each request to avoid bot detection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from playwright.async_api import async_playwright
from pydantic import Field

from houses.settings import settings
from serialization import SerializableAsDict, omit_when_none

logger = logging.getLogger(__name__)
class _ScrapeConstants:
    """The scraper's pacing and robustness tunables — one owner per
    the global-state gate."""

    HUMAN_DELAY_MIN_S = 3.0
    CHROME_START_ATTEMPTS = 100
    HUMAN_DELAY_MAX_S = 8.0
    CHROME_START_POLL_S = 0.1
    LOGIN_INDICATORS = [
        "sign in to continue",
        "verify you're a human",
        "unusual traffic",
        "unusual activity",
        "sign in to rightmove",
        'action="/signin"',
        'action="/login"',
        "please sign in",
    ]
    EXPECTED_FIELDS = (
        "address",
        "postcode",
        "bedrooms",
        "price",
        "latitude",
        "longitude",
    )


@dataclass
class RightmoveProperty:
    """Property data extracted from a Rightmove page.

    Owns the extraction of the numeric Rightmove ID from the property URL.
    """

    url: str
    address: str = ""
    postcode: str = ""
    bedrooms: int | None = None
    price: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    parse_errors: dict[str, str] = field(default_factory=dict)
    """Fields whose page-model structure or VALUE the parser could not
    interpret — carried out of the scrape so nothing downstream mistakes
    a failed parse for a legitimate absence."""
    # Extracted from url in __post_init__; deliberately not an __init__ parameter.
    rid: str = field(init=False)

    _RID_RE = re.compile(r"properties/(\d+)")

    def __post_init__(self) -> None:
        self.rid = self._extract_rid()

    def _extract_rid(self) -> str:
        m = self._RID_RE.search(self.url)
        return m.group(1) if m else ""

    @classmethod
    def rid_from_url(cls, url: str) -> str:
        """Extract the numeric Rightmove ID from a URL without constructing the full object."""
        m = cls._RID_RE.search(url)
        return m.group(1) if m else ""


CACHE_DIR = Path("data/rightmove_pages")
_CHROME_DATA_DIR = Path("/tmp/houses-chrome")
class _ChromeRuntime:
    """The scraper's Chrome subprocess state: the CDP process we
    spawned and whether we own it — one owner per the global-state gate."""

    process: asyncio.subprocess.Process | None = None
    started_here: bool = False
_LD_JSON_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_PRELOADED_RE = re.compile(
    r"window\.__PRELOADED_STATE__\s*=\s*({.*?});",
    re.DOTALL,
)
_INITIAL_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*({.*?});",
    re.DOTALL,
)
_MAP_COORDS_RE = re.compile(
    r'"latitude":\s*([\d.-]+),\s*"longitude"\s*:\s*([\d.-]+)',
)
_PAGE_MODEL_RE = re.compile(
    r"window\.__PAGE_MODEL\s*=\s*({.*?});",
    re.DOTALL,
)


async def _human_delay():
    delay = random.uniform(_ScrapeConstants.HUMAN_DELAY_MIN_S, _ScrapeConstants.HUMAN_DELAY_MAX_S)
    logger.info("Back-off: waiting %.1fs before Rightmove request", delay)
    await asyncio.sleep(delay)


_PRICE_MARKERS = frozenset({"poa", "price on application", "priced on application"})
"""Rightmove's explicit 'no listed price' markers — a legitimate None,
never a parse error."""


def _price_marker(raw: Any) -> bool:
    """True when the value is a recognized no-price marker (POA)."""
    return str(raw).strip().lower() in _PRICE_MARKERS


def _extract_price(raw: Any, *, field: str) -> float | None:
    """One price value: None when absent or a recognized marker (POA);
    the float when parseable; raises _PageModelError for a value the
    parser cannot interpret — a failed parse is an error, not absence."""
    if raw is None or _price_marker(raw):
        return None
    cleaned = _clean_price(raw)
    if cleaned is not None:
        return cleaned
    raise _PageModelError(f"{field} value {raw!r} is not parseable")


def _record_or_set_price(extracted, raw: Any):
    """Record a price parse error on the extract, or set the parsed price."""
    try:
        price = _extract_price(raw, field="price")
    except _PageModelError as e:
        return extracted.model_copy(update={"parse_errors": {"price": str(e)}})
    return extracted.model_copy(update={"price": price}) if price is not None else extracted


def _clean_price(raw: Any) -> float | None:
    """Parse a price value that may be a number, string, or contain formatting.
    Returns None ONLY for absent or marker values (via _extract_price);
    unparseable values raise there instead of silently vanishing."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = re.sub(r"[^0-9.]", "", str(raw))
    try:
        return float(s) if s else None
    except ValueError:
        return None


class _PropertyExtract(SerializableAsDict):
    """The property fields pulled from a Rightmove page's data sources.

    Only the fields a source actually yields are set; the dump omits the rest
    (``omit_when_none``), so the merged extraction keeps each source's key set.
    ``parse_errors`` is in-memory only — it is not part of the wire.
    """

    address: str | None = Field(default=None, exclude_if=omit_when_none)
    postcode: str | None = Field(default=None, exclude_if=omit_when_none)
    bedrooms: int | None = Field(default=None, exclude_if=omit_when_none)
    price: float | None = Field(default=None, exclude_if=omit_when_none)
    latitude: float | None = Field(default=None, exclude_if=omit_when_none)
    longitude: float | None = Field(default=None, exclude_if=omit_when_none)
    # Fields whose page-model STRUCTURE the parser could not traverse —
    # distinct from genuine absence (a field the page simply doesn't carry).
    # A structure mismatch means Rightmove changed the page layout.
    parse_errors: dict[str, str] = Field(default_factory=dict, exclude=True)

    def to_property(self, url: str) -> RightmoveProperty | None:
        """The property this extraction describes; None when it holds no value."""
        if not (set(self.to_dict()) - {"parse_errors"}):
            if self.parse_errors:
                # Nothing VALUE extracted, but the parser SAW a page it could
                # not interpret — the failures are the honest record; carry
                # them on the property instead of dropping them (2026-09-19).
                return RightmoveProperty(
                    url=url,
                    address=self.address or "",
                    postcode=self.postcode or "",
                    parse_errors=self.parse_errors,
                )
            return None
        return RightmoveProperty(
            url=url,
            address=self.address or "",
            postcode=self.postcode or "",
            bedrooms=self.bedrooms,
            price=self.price,
            latitude=self.latitude,
            longitude=self.longitude,
            parse_errors=self.parse_errors,
        )


class _PageModelError(Exception):
    """The page-model STRUCTURE is not what the parser expects (Rightmove
    changed the layout) — a typed error, never a silent None. Genuine
    field ABSENCE (the page carries no such value) stays None."""



def _extract_field(
    model: _PageModel,
    extracted: _PropertyExtract,
    getter: Callable[[], object],
    field: str,
) -> _PropertyExtract:
    """One page-model field: merge it when present, record the parse
    error otherwise — the per-field walk's shared step."""
    try:
        value = getter()
        if value is not None:
            return extracted.model_copy(update={field: value})  # dynamic field name, the helper's contract
    except _PageModelError as e:
        return extracted.model_copy(update={"parse_errors": {**extracted.parse_errors, field: str(e)}})
    return extracted


def _extract_pair(
    model: _PageModel,
    extracted: _PropertyExtract,
    getter: Callable[[], tuple[str, str] | tuple[float, float] | None],
    field_a: str,
    field_b: str,
) -> _PropertyExtract:
    """A two-field page-model merge (address/coords): split the tuple into
    its two record fields, recording the parse error otherwise."""
    try:
        pair = getter()
        if pair is not None:
            return cast(
                _PropertyExtract,
                extracted.model_copy(update={field_a: pair[0], field_b: pair[1]}),  # dynamic field names
            )
    except _PageModelError as e:
        return extracted.model_copy(update={"parse_errors": {**extracted.parse_errors, field_a: str(e)}})
    return extracted


def _parse_page_model(html: str) -> _PropertyExtract:
    """Extract property data from window.__PAGE_MODEL (Rightmove's primary data format).

    The model is a JSON object where ``data`` is a string containing a JSON array.
    ``data[0]`` is a schema, ``data[0].propertyData`` indexes into the array for the
    property schema, and its fields (address, prices, location, bedrooms) recursively
    index into the array for the actual values.
    """
    m = _PAGE_MODEL_RE.search(html)
    if not m:
        return _PropertyExtract()
    try:
        pm = json.loads(m.group(1))
        data = json.loads(pm["data"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return _PropertyExtract()

    try:
        prop = data[data[0]["propertyData"]]
    except (IndexError, KeyError, TypeError):
        return _PropertyExtract()

    extracted = _PropertyExtract()
    model = _PageModel(data, prop)

    # Address + postcode
    extracted = _extract_pair(model, extracted, model.address, field_a="address", field_b="postcode")

    # Price
    extracted = _extract_field(model, extracted, model.price, "price")

    # Bedrooms
    extracted = _extract_field(model, extracted, model.bedrooms, "bedrooms")

    # Location (lat/lng)
    extracted = _extract_pair(model, extracted, model.location, field_a="latitude", field_b="longitude")

    return extracted


class _PageModel:
    """The window.__PAGE_MODEL array paired with the property entry that
    indexes into it — the two values every page-model field resolves
    through, so the clump lives here rather than in each helper's signature.

    A structure the parser cannot traverse is a typed _PageModelError
    (Rightmove changed the page); None means the page genuinely carries
    no such field.
    """

    def __init__(self, data: Any, prop: Any) -> None:
        self.data = data
        self.prop = prop

    type _PageModelAddress = tuple[str, str]  # (address, postcode)

    def address(self) -> _PageModelAddress | None:
        """(address, postcode) from the page model."""
        try:
            addr_schema = self.data[self.prop["address"]]
            addr_parts = [self.data[addr_schema["displayAddress"]]]
            outcode = self.data[addr_schema["outcode"]]
            incode = self.data[addr_schema["incode"]]
            return addr_parts[0], f"{outcode} {incode}"
        except (IndexError, KeyError, TypeError) as e:
            raise _PageModelError(f"address schema missing ({e})") from e

    def price(self) -> float | None:
        """Price from the page model.

        Structure mismatch → _PageModelError; a present but unparseable
        value (e.g. 'POA') is genuine absence → None."""
        try:
            price_schema = self.data[self.prop["prices"]]
        except (IndexError, KeyError, TypeError) as e:
            raise _PageModelError(f"price schema missing ({e})") from e
        return _extract_price(self.data[price_schema["primaryPrice"]], field="price")

    def bedrooms(self) -> int | None:
        """Bedroom count from the page model.

        Structure mismatch → _PageModelError; a non-integer value is
        genuine absence → None."""
        try:
            beds = self.data[self.prop["bedrooms"]]
        except (IndexError, KeyError, TypeError) as e:
            raise _PageModelError(f"bedrooms schema missing ({e})") from e
        return beds if isinstance(beds, int) else None

    type _PageModelLocation = tuple[float, float]  # (lat, lng)

    def location(self) -> _PageModelLocation | None:
        """(lat, lng) from the page model.

        Structure mismatch → _PageModelError; non-numeric coordinates are
        genuine absence → None."""
        try:
            loc_schema = self.data[self.prop["location"]]
            lat = self.data[loc_schema["latitude"]]
            lng = self.data[loc_schema["longitude"]]
        except (IndexError, KeyError, TypeError) as e:
            raise _PageModelError(f"location schema missing ({e})") from e
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            return float(lat), float(lng)
        return None


def _merge_missing(result: _PropertyExtract, source: _PropertyExtract) -> _PropertyExtract:
    """Fill fields absent from result from a secondary extraction source."""
    return _PropertyExtract(
        address=result.address if result.address is not None else source.address,
        postcode=result.postcode if result.postcode is not None else source.postcode,
        bedrooms=result.bedrooms if result.bedrooms is not None else source.bedrooms,
        price=result.price if result.price is not None else source.price,
        latitude=result.latitude if result.latitude is not None else source.latitude,
        longitude=result.longitude if result.longitude is not None else source.longitude,
        parse_errors={**result.parse_errors, **source.parse_errors},
    )


class _PageHtml:
    """One Rightmove page: its HTML plus the ``window.__PAGE_MODEL``
    extraction, read across the sources Rightmove ships the property in.

    Owns the page text and the fallback merge: JSON-LD, preloaded state,
    map coordinates, and the DOM fallbacks — the state every parse step
    reads.
    """

    def __init__(self, html: str, page_model: _PropertyExtract) -> None:
        self.html = html
        self.page_model = page_model

    # lucidlint: ignore latent-class the fallback walk reads the PAGE (DOM + preloaded state), not only the record
    def to_property(self, url: str) -> RightmoveProperty | None:
        """Extract property data from this page's HTML.

        Tries data sources in order of preference:
          1. window.__PAGE_MODEL (Rightmove's primary data store)
          2. JSON-LD structured data (schema.org)
          3. window.__PRELOADED_STATE__ / window.__INITIAL_STATE__
          4. Map coordinate regex fallback
          5. DOM extraction fallback

        Merges results across sources — e.g. lat/lon from preloaded state
        may fill gaps left by JSON-LD.  Returns ``None`` when no data can be
        extracted from the HTML.
        """
        result = self.page_model

        # 2. JSON-LD (fills gaps)
        result = _merge_missing(result, self._json_ld())

        # 3. Preloaded state (fills bedrooms, lat/lon that JSON-LD may lack)
        result = _merge_missing(result, self._preloaded_state())

        # 4. Map coords fallback
        if result.latitude is None:
            result = _merge_missing(result, self._map_coords())

        # 5. DOM extraction fallback
        if result.address is None:
            addr = self._testid_text("address-label")
            if addr:
                result = result.model_copy(update={"address": addr})
        if result.bedrooms is None:
            beds = self._bedrooms_from_dom()
            if beds is not None:
                result = result.model_copy(update={"bedrooms": beds})

        return result.to_property(url)

    def _json_ld(self) -> _PropertyExtract:
        """Extract property data from JSON-LD structured data."""
        m = _LD_JSON_RE.search(self.html)
        if not m:
            return _PropertyExtract()
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return _PropertyExtract()

        extracted = _PropertyExtract()

        addr = data.get("address") or {}
        street = addr.get("streetAddress", "")
        locality = addr.get("addressLocality", "")
        postcode = addr.get("postalCode", "")
        parts = [p for p in [street, locality, postcode] if p]
        if parts:
            extracted = extracted.model_copy(update={"address": ", ".join(parts)})
        if postcode:
            extracted = extracted.model_copy(update={"postcode": postcode})

        offers = data.get("offers") or {}
        extracted = _record_or_set_price(extracted, offers.get("price"))

        geo = data.get("geo") or {}
        lat = geo.get("latitude")
        lng = geo.get("longitude")
        if lat is not None and lng is not None:
            extracted = extracted.model_copy(update={"latitude": float(lat), "longitude": float(lng)})

        return extracted

    def _preloaded_state(self) -> _PropertyExtract:
        """Extract from window.__PRELOADED_STATE__ (Rightmove React app)."""
        for pattern in [_PRELOADED_RE, _INITIAL_STATE_RE]:
            m = pattern.search(self.html)
            if not m:
                continue
            try:
                state = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue

            extracted = _PropertyExtract()

            pd = state.get("propertyData") or state.get("property") or {}
            if pd.get("address"):
                extracted = extracted.model_copy(update={"address": pd["address"]})
            if pd.get("bedrooms") is not None:
                extracted = extracted.model_copy(update={"bedrooms": int(pd["bedrooms"])})
            extracted = _record_or_set_price(extracted, pd.get("price"))
            loc = pd.get("location") or {}
            lat = loc.get("latitude")
            lng = loc.get("longitude")
            if lat is not None and lng is not None:
                extracted = extracted.model_copy(update={"latitude": float(lat), "longitude": float(lng)})

            return extracted

        return _PropertyExtract()

    def _map_coords(self) -> _PropertyExtract:
        """Fallback: extract lat/lng from inline map data in script tags."""
        m = _MAP_COORDS_RE.search(self.html)
        if m:
            return _PropertyExtract(latitude=float(m.group(1)), longitude=float(m.group(2)))
        return _PropertyExtract()

    def _testid_text(self, testid: str) -> str:
        """Extract text content from a data-testid element via regex."""
        m = re.search(
            rf'data-testid=["\']{testid}["\'][^>]*>(.*?)</',
            self.html,
            re.DOTALL,
        )
        if m:
            text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            return text
        return ""

    def _bedrooms_from_dom(self) -> int | None:
        """Find the bedroom count from common DOM patterns."""
        m = re.search(r"(\d+)\s*bedroom", self.html, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None


def _parse_html(html: str, url: str) -> RightmoveProperty | None:
    """Extract property data from a Rightmove page HTML — see ``_PageHtml``."""
    return _PageHtml(html, _parse_page_model(html)).to_property(url)


def _is_login_wall(html: str) -> bool:
    """Check if the page content looks like a Rightmove login/verification wall."""
    lower = html.lower()
    for phrase in _ScrapeConstants.LOGIN_INDICATORS:
        if phrase in lower:
            return True
    # Check page title
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if m:
        title = m.group(1).lower()
        if any(kw in title for kw in ("sign in", "verify", "unusual")):
            return True
    return False


def _chrome_url() -> str:
    return f"http://127.0.0.1:{settings.rightmove_chrome_port}"


def _is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


async def _ensure_chrome():
    """Start a headless Chrome with remote debugging if not already running."""
    
    if _is_port_open(settings.rightmove_chrome_port):
        return
    if _ChromeRuntime.started_here and _ChromeRuntime.process is not None and _ChromeRuntime.process.returncode is None:
        return  # We already started it — still running

    _CHROME_DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Starting google-chrome on port %s for CDP",
        settings.rightmove_chrome_port,
    )
    _ChromeRuntime.process = await asyncio.create_subprocess_exec(
        "google-chrome",
        f"--remote-debugging-port={settings.rightmove_chrome_port}",
        f"--user-data-dir={_CHROME_DATA_DIR}",
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-extensions",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    _ChromeRuntime.started_here = True

    for _ in range(_ScrapeConstants.CHROME_START_ATTEMPTS):
        if _is_port_open(settings.rightmove_chrome_port):
            logger.info("Chrome ready on port %s", settings.rightmove_chrome_port)
            return
        await asyncio.sleep(_ScrapeConstants.CHROME_START_POLL_S)

    logger.error("Chrome failed to start within 10s on port %s", settings.rightmove_chrome_port)


async def _stop_chrome_process(proc: asyncio.subprocess.Process) -> None:
    """Terminate, then kill, the spawned Chrome; never raises (best-effort).

    ``stop_chrome`` still runs the pkill fallback afterwards, so a process
    that survives SIGKILL is cleaned up there rather than here.
    """
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except (TimeoutError, ProcessLookupError) as e:
        logger.debug("Chrome ignored SIGTERM — escalating to SIGKILL: %s", e)
        try:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except (TimeoutError, ProcessLookupError) as e2:
            logger.debug("Chrome still alive after SIGKILL — pkill fallback will clean up: %s", e2)
            return


async def _pkill_owned_chrome(fingerprint: str) -> None:
    """pkill any Chrome processes we own; never raises (best-effort)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pkill",
            "-f",
            fingerprint,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except (OSError, TimeoutError) as e:
        # pkill is absent, or would not die within the window: best-effort
        # cleanup, so the caller only sees a debug line.
        logger.debug("pkill fallback failed for %s (Chrome may linger): %s", fingerprint, e)
        return


async def stop_chrome():
    """Kill the Chrome instance we spawned, using user-data-dir as a fingerprint."""
    if not _ChromeRuntime.started_here:
        return

    fingerprint = str(_CHROME_DATA_DIR)
    logger.info("Shutting down Chrome (data dir: %s)", fingerprint)

    if _ChromeRuntime.process is not None and _ChromeRuntime.process.returncode is None:
        await _stop_chrome_process(_ChromeRuntime.process)

    # Also pkill any remaining chrome processes we own
    await _pkill_owned_chrome(fingerprint)

    _ChromeRuntime.process = None
    _ChromeRuntime.started_here = False


async def _fetch_via_chrome(url: str) -> str:
    """Connect to Chrome via CDP, navigate to URL, return page HTML."""
    await _ensure_chrome()

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(_chrome_url())
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            html = await page.content()
            return html
        finally:
            await page.close()



def _report_missing(result: RightmoveProperty, rid: str) -> None:
    missing = [k for k in _ScrapeConstants.EXPECTED_FIELDS if not getattr(result, k, None)]
    if missing:
        found = [k for k in _ScrapeConstants.EXPECTED_FIELDS if getattr(result, k, None)]
        logger.warning(
            "Rightmove scraper for %s: partial extraction — missing %s, found %s",
            rid,
            missing,
            found,
        )


def _parsed_from(html: str, url: str, rid: str) -> RightmoveProperty | None:
    """Parse cached/fetched HTML and report any missing fields."""
    result = _parse_html(html, url)
    if result:
        _report_missing(result, rid)
    return result


def _write_cache(cache_file: Path, html: str) -> None:
    """Persist fetched HTML to the page cache, creating the cache dir on first use."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(html, encoding="utf-8")


async def scrape(url: str, _page_path: str | None = None) -> RightmoveProperty | None:
    """Return property details for a Rightmove URL.

    ``_page_path`` — optional path to a sample HTML file (for tests).
    When omitted, falls back to ``settings.rightmove_sample_page``.

    Cache is checked first. On a cache miss:
      * **Normal mode** — fetches the page via Chrome CDP, caches it, returns
        parsed data. Applies randomised back-off.
      * **Offline mode** (``rightmove_scraper_offline=True``) — returns
        ``None`` with a warning. Tests must pre-populate the cache.

    Returns a ``RightmoveProperty`` or ``None``.
    """
    rid = RightmoveProperty.rid_from_url(url)
    if not rid:
        logger.warning("Could not extract Rightmove ID from URL: %s", url)
        return None

    # 1. Page cache
    cache_file = CACHE_DIR / f"{rid}.html"
    if cache_file.exists():
        logger.info("Using cached Rightmove page for %s", rid)
        return _parsed_from(cache_file.read_text(encoding="utf-8"), url, rid)

    # 1b. Offline mode: never fabricate a page — a cache miss IS None
    # (the docstring contract; the sample step below is for tests that
    # pass _page_path explicitly, not for offline production runs).
    if settings.rightmove_scraper_offline:
        logger.warning("Rightmove scraper offline mode — no cached page for %s", url)
        return None

    # 2. Sample page (development / tests)
    sample = _page_path or settings.rightmove_sample_page
    if sample:
        path = Path(sample)
        if not path.exists():
            logger.warning("Rightmove sample page not found: %s", path)
            return None
        logger.info("Using Rightmove sample page: %s", path)
        return _parsed_from(path.read_text(encoding="utf-8"), url, rid)


    # 4. Normal mode — fetch via Chrome CDP
    await _human_delay()
    html = await _fetch_via_chrome(url)

    if _is_login_wall(html):
        _write_cache(cache_file, html)
        logger.warning(
            "Rightmove returned a login/verification page for %s. "
            "Please open Chrome in non-headless mode, navigate to "
            "Rightmove and sign in, then try again.",
            url,
        )
        return None

    if html:
        _write_cache(cache_file, html)
        logger.info("Cached Rightmove page to %s", cache_file)

    return _parsed_from(html, url, rid) if html else None
