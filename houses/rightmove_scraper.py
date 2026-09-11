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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from houses.settings import settings

logger = logging.getLogger(__name__)
_HUMAN_DELAY_MIN_S = 3.0
_CHROME_START_ATTEMPTS = 100
_HUMAN_DELAY_MAX_S = 8.0
_CHROME_START_POLL_S = 0.1


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
_CHROME_PROCESS: asyncio.subprocess.Process | None = None
_WE_STARTED_CHROME: bool = False
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
    delay = random.uniform(_HUMAN_DELAY_MIN_S, _HUMAN_DELAY_MAX_S)
    logger.info("Back-off: waiting %.1fs before Rightmove request", delay)
    await asyncio.sleep(delay)


def _clean_price(raw: Any) -> float | None:
    """Parse a price value that may be a number, string, or contain formatting."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = re.sub(r"[^0-9.]", "", str(raw))
    try:
        return float(s) if s else None
    except ValueError:
        return None


@dataclass(frozen=True)
class _PropertyExtractJson:
    """The property fields pulled from a Rightmove page's data sources.

    Only the fields a source actually yields are set; to_dict omits the
    rest, so the merged extraction keeps each source's key set.
    """

    address: str | None = None
    postcode: str | None = None
    bedrooms: int | None = None
    price: float | None = None
    latitude: float | None = None
    longitude: float | None = None

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        d = {}
        for k in ("address", "postcode", "bedrooms", "price", "latitude", "longitude"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d


def _parse_json_ld(html: str) -> _PropertyExtractJson:
    """Extract property data from JSON-LD structured data."""
    m = _LD_JSON_RE.search(html)
    if not m:
        return _PropertyExtractJson()
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return _PropertyExtractJson()

    extracted = _PropertyExtractJson()

    addr = data.get("address") or {}
    street = addr.get("streetAddress", "")
    locality = addr.get("addressLocality", "")
    postcode = addr.get("postalCode", "")
    parts = [p for p in [street, locality, postcode] if p]
    if parts:
        extracted = replace(extracted, address=", ".join(parts))
    if postcode:
        extracted = replace(extracted, postcode=postcode)

    offers = data.get("offers") or {}
    price = _clean_price(offers.get("price"))
    if price is not None:
        extracted = replace(extracted, price=price)

    geo = data.get("geo") or {}
    lat = geo.get("latitude")
    lng = geo.get("longitude")
    if lat is not None and lng is not None:
        extracted = replace(extracted, latitude=float(lat), longitude=float(lng))

    return extracted


def _parse_preloaded_state(html: str) -> _PropertyExtractJson:
    """Extract from window.__PRELOADED_STATE__ (Rightmove React app)."""
    for pattern in [_PRELOADED_RE, _INITIAL_STATE_RE]:
        m = pattern.search(html)
        if not m:
            continue
        try:
            state = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue

        extracted = _PropertyExtractJson()

        pd = state.get("propertyData") or state.get("property") or {}
        if pd.get("address"):
            extracted = replace(extracted, address=pd["address"])
        if pd.get("bedrooms") is not None:
            extracted = replace(extracted, bedrooms=int(pd["bedrooms"]))
        price = _clean_price(pd.get("price"))
        if price is not None:
            extracted = replace(extracted, price=price)
        loc = pd.get("location") or {}
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        if lat is not None and lng is not None:
            extracted = replace(extracted, latitude=float(lat), longitude=float(lng))

        return extracted

    return _PropertyExtractJson()


def _parse_map_coords(html: str) -> _PropertyExtractJson:
    """Fallback: extract lat/lng from inline map data in script tags."""
    m = _MAP_COORDS_RE.search(html)
    if m:
        return _PropertyExtractJson(latitude=float(m.group(1)), longitude=float(m.group(2)))
    return _PropertyExtractJson()


def _parse_page_model(html: str) -> _PropertyExtractJson:
    """Extract property data from window.__PAGE_MODEL (Rightmove's primary data format).

    The model is a JSON object where ``data`` is a string containing a JSON array.
    ``data[0]`` is a schema, ``data[0].propertyData`` indexes into the array for the
    property schema, and its fields (address, prices, location, bedrooms) recursively
    index into the array for the actual values.
    """
    m = _PAGE_MODEL_RE.search(html)
    if not m:
        return _PropertyExtractJson()
    try:
        pm = json.loads(m.group(1))
        data = json.loads(pm["data"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return _PropertyExtractJson()

    try:
        prop = data[data[0]["propertyData"]]
    except (IndexError, KeyError, TypeError):
        return _PropertyExtractJson()

    extracted = _PropertyExtractJson()

    # Address
    address = _page_model_address(data, prop)
    if address is not None:
        extracted = replace(extracted, address=address[0], postcode=address[1])

    # Price
    price = _page_model_price(data, prop)
    if price is not None:
        extracted = replace(extracted, price=price)

        # lucidlint: ignore duplicate-block field-merge accordion — each 3-line block merges a different page-model
        # Bedrooms
    bedrooms = _page_model_bedrooms(data, prop)
    if bedrooms is not None:
        extracted = replace(extracted, bedrooms=bedrooms)

    # Location (lat/lng)
    location = _page_model_location(data, prop)
    if location is not None:
        extracted = replace(extracted, latitude=location[0], longitude=location[1])

    return extracted


_PageModelAddress = tuple[str, str]  # (address, postcode)
_PageModelLocation = tuple[float, float]  # (lat, lng)


def _page_model_address(data: Any, prop: Any) -> _PageModelAddress | None:
    """(address, postcode) from the page model, or None when the fields are absent."""
    try:
        addr_schema = data[prop["address"]]
        addr_parts = [data[addr_schema["displayAddress"]]]
        outcode = data[addr_schema["outcode"]]
        incode = data[addr_schema["incode"]]
        return addr_parts[0], f"{outcode} {incode}"
    except (IndexError, KeyError, TypeError) as e:
        logger.debug("address/postcode fields absent from the page model (skipped): %s", e)
        return None


def _page_model_price(data: Any, prop: Any) -> float | None:
    """Price from the page model, or None when the field is absent/unparseable."""
    try:
        price_schema = data[prop["prices"]]
        return _clean_price(data[price_schema["primaryPrice"]])
    except (IndexError, KeyError, TypeError) as e:
        logger.debug("price field absent from the page model (skipped): %s", e)
        return None


def _page_model_bedrooms(data: Any, prop: Any) -> int | None:
    """Bedroom count from the page model, or None when absent or not an integer."""
    try:
        beds = data[prop["bedrooms"]]
        if isinstance(beds, int):
            return beds
    except (IndexError, KeyError, TypeError) as e:
        logger.debug("bedrooms field absent from the page model (skipped): %s", e)
        return None
    return None


def _page_model_location(data: Any, prop: Any) -> _PageModelLocation | None:
    """(lat, lng) from the page model, or None when absent or non-numeric."""
    try:
        loc_schema = data[prop["location"]]
        lat = data[loc_schema["latitude"]]
        lng = data[loc_schema["longitude"]]
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            return float(lat), float(lng)
    except (IndexError, KeyError, TypeError) as e:
        logger.debug("location fields absent from the page model (skipped): %s", e)
        return None
    return None


def _merge_missing(result: _PropertyExtractJson, source: _PropertyExtractJson) -> _PropertyExtractJson:
    """Fill fields absent from result from a secondary extraction source."""
    return _PropertyExtractJson(
        address=result.address if result.address is not None else source.address,
        postcode=result.postcode if result.postcode is not None else source.postcode,
        bedrooms=result.bedrooms if result.bedrooms is not None else source.bedrooms,
        price=result.price if result.price is not None else source.price,
        latitude=result.latitude if result.latitude is not None else source.latitude,
        longitude=result.longitude if result.longitude is not None else source.longitude,
    )


def _parse_html(html: str, url: str) -> RightmoveProperty | None:
    """Extract property data from a Rightmove page HTML.

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
    result = _parse_page_model(html)

    # 2. JSON-LD (fills gaps)
    result = _merge_missing(result, _parse_json_ld(html))

    # 3. Preloaded state (fills bedrooms, lat/lon that JSON-LD may lack)
    result = _merge_missing(result, _parse_preloaded_state(html))

    # 4. Map coords fallback
    if result.latitude is None:
        result = _merge_missing(result, _parse_map_coords(html))

    # 5. DOM extraction fallback
    if result.address is None:
        addr = _extract_by_testid(html, "address-label")
        if addr:
            result = replace(result, address=addr)
    if result.bedrooms is None:
        beds = _extract_bedrooms_from_html(html)
        if beds is not None:
            result = replace(result, bedrooms=beds)

    if result == _PropertyExtractJson():
        return None
    return RightmoveProperty(
        url=url,
        address=result.address or "",
        postcode=result.postcode or "",
        bedrooms=result.bedrooms,
        price=result.price,
        latitude=result.latitude,
        longitude=result.longitude,
    )


def _extract_by_testid(html: str, testid: str) -> str:
    """Extract text content from a data-testid element via regex."""
    m = re.search(
        rf'data-testid=["\']{testid}["\'][^>]*>(.*?)</',
        html,
        re.DOTALL,
    )
    if m:
        text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return text
    return ""


def _extract_bedrooms_from_html(html: str) -> int | None:
    """Find the bedroom count from common DOM patterns."""
    m = re.search(r"(\d+)\s*bedroom", html, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


_LOGIN_INDICATORS = [
    "sign in to continue",
    "verify you're a human",
    "unusual traffic",
    "unusual activity",
    "sign in to rightmove",
    'action="/signin"',
    'action="/login"',
    "please sign in",
]


def _is_login_wall(html: str) -> bool:
    """Check if the page content looks like a Rightmove login/verification wall."""
    lower = html.lower()
    for phrase in _LOGIN_INDICATORS:
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
    global _CHROME_PROCESS, _WE_STARTED_CHROME

    if _is_port_open(settings.rightmove_chrome_port):
        return
    if _WE_STARTED_CHROME and _CHROME_PROCESS is not None and _CHROME_PROCESS.returncode is None:
        return  # We already started it — still running

    _CHROME_DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Starting google-chrome on port %s for CDP",
        settings.rightmove_chrome_port,
    )
    _CHROME_PROCESS = await asyncio.create_subprocess_exec(
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
    _WE_STARTED_CHROME = True

    for _ in range(_CHROME_START_ATTEMPTS):
        if _is_port_open(settings.rightmove_chrome_port):
            logger.info("Chrome ready on port %s", settings.rightmove_chrome_port)
            return
        await asyncio.sleep(_CHROME_START_POLL_S)

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
    # lucidlint: ignore broad-except best-effort boundary — pkill fallback never raises (docstring contract)
    except Exception as e:
        logger.debug("pkill fallback failed for %s (Chrome may linger): %s", fingerprint, e)
        return


async def stop_chrome():
    """Kill the Chrome instance we spawned, using user-data-dir as a fingerprint."""
    global _CHROME_PROCESS, _WE_STARTED_CHROME
    if not _WE_STARTED_CHROME:
        return

    fingerprint = str(_CHROME_DATA_DIR)
    logger.info("Shutting down Chrome (data dir: %s)", fingerprint)

    if _CHROME_PROCESS is not None and _CHROME_PROCESS.returncode is None:
        await _stop_chrome_process(_CHROME_PROCESS)

    # Also pkill any remaining chrome processes we own
    await _pkill_owned_chrome(fingerprint)

    _CHROME_PROCESS = None
    _WE_STARTED_CHROME = False


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


_EXPECTED_FIELDS = ("address", "postcode", "bedrooms", "price", "latitude", "longitude")


def _report_missing(result: RightmoveProperty, rid: str) -> None:
    missing = [k for k in _EXPECTED_FIELDS if not getattr(result, k, None)]
    if missing:
        found = [k for k in _EXPECTED_FIELDS if getattr(result, k, None)]
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

    # 2. Sample page (development / tests)
    sample = _page_path or settings.rightmove_sample_page
    if sample:
        path = Path(sample)
        if not path.exists():
            logger.warning("Rightmove sample page not found: %s", path)
            return None
        logger.info("Using Rightmove sample page: %s", path)
        return _parsed_from(path.read_text(encoding="utf-8"), url, rid)

    # 3. Offline mode — fail fast instead of starting Chrome
    if settings.rightmove_scraper_offline:
        logger.warning(
            "No cached Rightmove page for %s and offline mode is enabled. Pre-populate the cache before running tests.",
            rid,
        )
        return None

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
