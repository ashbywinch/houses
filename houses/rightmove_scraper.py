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
from pathlib import Path
from typing import Any

from houses.config import settings

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/rightmove_pages")
_CHROME_DATA_DIR = Path("/tmp/houses-chrome")
_CHROME_PROCESS: asyncio.subprocess.Process | None = None
_WE_STARTED_CHROME: bool = False
_RID_RE = re.compile(r"properties/(\d+)")
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


def _rid_from_url(url: str) -> str:
    m = _RID_RE.search(url)
    return m.group(1) if m else ""


async def _human_delay():
    delay = random.uniform(3.0, 8.0)
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


def _parse_json_ld(html: str) -> dict[str, Any]:
    """Extract property data from JSON-LD structured data."""
    m = _LD_JSON_RE.search(html)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}

    result: dict[str, Any] = {}

    addr = data.get("address") or {}
    street = addr.get("streetAddress", "")
    locality = addr.get("addressLocality", "")
    postcode = addr.get("postalCode", "")
    parts = [p for p in [street, locality, postcode] if p]
    if parts:
        result["address"] = ", ".join(parts)
    if postcode:
        result["postcode"] = postcode

    offers = data.get("offers") or {}
    price = _clean_price(offers.get("price"))
    if price is not None:
        result["price"] = price

    geo = data.get("geo") or {}
    lat = geo.get("latitude")
    lng = geo.get("longitude")
    if lat is not None and lng is not None:
        result["latitude"] = float(lat)
        result["longitude"] = float(lng)

    return result


def _parse_preloaded_state(html: str) -> dict[str, Any]:
    """Extract from window.__PRELOADED_STATE__ (Rightmove React app)."""
    for pattern in [_PRELOADED_RE, _INITIAL_STATE_RE]:
        m = pattern.search(html)
        if not m:
            continue
        try:
            state = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue

        result: dict[str, Any] = {}

        pd = state.get("propertyData") or state.get("property") or {}
        if pd.get("address"):
            result["address"] = pd["address"]
        if pd.get("bedrooms") is not None:
            result["bedrooms"] = int(pd["bedrooms"])
        price = _clean_price(pd.get("price"))
        if price is not None:
            result["price"] = price
        loc = pd.get("location") or {}
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        if lat is not None and lng is not None:
            result["latitude"] = float(lat)
            result["longitude"] = float(lng)

        return result

    return {}


def _parse_map_coords(html: str) -> dict[str, Any]:
    """Fallback: extract lat/lng from inline map data in script tags."""
    m = _MAP_COORDS_RE.search(html)
    if m:
        return {"latitude": float(m.group(1)), "longitude": float(m.group(2))}
    return {}


def _parse_page_model(html: str) -> dict[str, Any]:
    """Extract property data from window.__PAGE_MODEL (Rightmove's primary data format).

    The model is a JSON object where ``data`` is a string containing a JSON array.
    ``data[0]`` is a schema, ``data[0].propertyData`` indexes into the array for the
    property schema, and its fields (address, prices, location, bedrooms) recursively
    index into the array for the actual values.
    """
    m = _PAGE_MODEL_RE.search(html)
    if not m:
        return {}
    try:
        pm = json.loads(m.group(1))
        data = json.loads(pm["data"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}

    try:
        prop = data[data[0]["propertyData"]]
    except (IndexError, KeyError, TypeError):
        return {}

    result: dict[str, Any] = {}

    # Address
    try:
        addr_schema = data[prop["address"]]
        addr_parts = [data[addr_schema["displayAddress"]]]
        outcode = data[addr_schema["outcode"]]
        incode = data[addr_schema["incode"]]
        result["address"] = addr_parts[0]
        result["postcode"] = f"{outcode} {incode}"
    except (IndexError, KeyError, TypeError):
        pass

    # Price
    try:
        price_schema = data[prop["prices"]]
        price = _clean_price(data[price_schema["primaryPrice"]])
        if price is not None:
            result["price"] = price
    except (IndexError, KeyError, TypeError):
        pass

    # Bedrooms
    try:
        beds = data[prop["bedrooms"]]
        if isinstance(beds, int):
            result["bedrooms"] = beds
    except (IndexError, KeyError, TypeError):
        pass

    # Location (lat/lng)
    try:
        loc_schema = data[prop["location"]]
        lat = data[loc_schema["latitude"]]
        lng = data[loc_schema["longitude"]]
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            result["latitude"] = float(lat)
            result["longitude"] = float(lng)
    except (IndexError, KeyError, TypeError):
        pass

    return result


def _parse_html(html: str, url: str) -> dict[str, Any]:
    """Extract property data from a Rightmove page HTML.

    Tries data sources in order of preference:
      1. window.__PAGE_MODEL (Rightmove's primary data store)
      2. JSON-LD structured data (schema.org)
      3. window.__PRELOADED_STATE__ / window.__INITIAL_STATE__
      4. Map coordinate regex fallback
      5. DOM extraction fallback

    Merges results across sources — e.g. lat/lon from preloaded state
    may fill gaps left by JSON-LD.
    """
    if not html.strip():
        return {}

    result: dict[str, Any] = {}

    # 1. __PAGE_MODEL (most reliable for modern Rightmove)
    pm = _parse_page_model(html)
    result.update(pm)

    # 2. JSON-LD (fills gaps)
    ld = _parse_json_ld(html)
    for key in ("address", "postcode", "bedrooms", "price", "latitude", "longitude"):
        if key not in result and key in ld:
            result[key] = ld[key]

    # 3. Preloaded state (fills bedrooms, lat/lon that JSON-LD may lack)
    ps = _parse_preloaded_state(html)
    for key in ("address", "postcode", "bedrooms", "price", "latitude", "longitude"):
        if key not in result and key in ps:
            result[key] = ps[key]

    # 4. Map coords fallback
    if "latitude" not in result:
        coords = _parse_map_coords(html)
        result.update(coords)

    # 5. DOM extraction fallback
    if "address" not in result:
        addr = _extract_by_testid(html, "address-label")
        if addr:
            result["address"] = addr
    if "bedrooms" not in result:
        beds = _extract_bedrooms_from_html(html)
        if beds is not None:
            result["bedrooms"] = beds

    return result


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

    for _ in range(100):
        if _is_port_open(settings.rightmove_chrome_port):
            logger.info("Chrome ready on port %s", settings.rightmove_chrome_port)
            return
        await asyncio.sleep(0.1)

    logger.error("Chrome failed to start within 10s on port %s", settings.rightmove_chrome_port)


async def stop_chrome():
    """Kill the Chrome instance we spawned, using user-data-dir as a fingerprint."""
    global _CHROME_PROCESS, _WE_STARTED_CHROME
    if not _WE_STARTED_CHROME:
        return

    fingerprint = str(_CHROME_DATA_DIR)
    logger.info("Shutting down Chrome (data dir: %s)", fingerprint)

    if _CHROME_PROCESS is not None and _CHROME_PROCESS.returncode is None:
        _CHROME_PROCESS.terminate()
        try:
            await asyncio.wait_for(_CHROME_PROCESS.wait(), timeout=3.0)
        except (TimeoutError, ProcessLookupError):
            try:
                _CHROME_PROCESS.kill()
                await asyncio.wait_for(_CHROME_PROCESS.wait(), timeout=2.0)
            except (TimeoutError, ProcessLookupError):
                pass

    # Also pkill any remaining chrome processes we own
    try:
        proc = await asyncio.create_subprocess_exec(
            "pkill",
            "-f",
            fingerprint,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except Exception:
        pass

    _CHROME_PROCESS = None
    _WE_STARTED_CHROME = False


async def _fetch_via_chrome(url: str) -> str:
    """Connect to Chrome via CDP, navigate to URL, return page HTML."""
    await _ensure_chrome()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error(
            "playwright is required for Rightmove scraping. Run: pip install playwright && playwright install chromium"
        )
        return ""

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


def _report_missing(result: dict[str, Any], rid: str) -> None:
    missing = [k for k in _EXPECTED_FIELDS if k not in result]
    if missing:
        found = [k for k in _EXPECTED_FIELDS if k in result]
        logger.warning(
            "Rightmove scraper for %s: partial extraction — missing %s, found %s",
            rid,
            missing,
            found,
        )


async def scrape(url: str, _page_path: str | None = None) -> dict[str, Any]:
    """Return property details for a Rightmove URL.

    ``_page_path`` — optional path to a sample HTML file (for tests).
    When omitted, falls back to ``settings.rightmove_sample_page``.

    Cache is checked first. On a cache miss:
      * **Normal mode** — fetches the page via Chrome CDP, caches it, returns
        parsed data. Applies randomised back-off.
      * **Offline mode** (``rightmove_scraper_offline=True``) — returns an
        empty dict with a warning. Tests must pre-populate the cache.

    Returns a dict with keys: address, postcode, bedrooms, price,
    latitude, longitude (or ``{"_error": "login_required"}``).
    """
    rid = _rid_from_url(url)
    if not rid:
        logger.warning("Could not extract Rightmove ID from URL: %s", url)
        return {}

    # 1. Page cache
    cache_file = CACHE_DIR / f"{rid}.html"
    if cache_file.exists():
        logger.info("Using cached Rightmove page for %s", rid)
        html = cache_file.read_text(encoding="utf-8")
        result = _parse_html(html, url)
        _report_missing(result, rid)
        return result

    # 2. Sample page (development / tests)
    sample = _page_path or settings.rightmove_sample_page
    if sample:
        path = Path(sample)
        if not path.exists():
            logger.warning("Rightmove sample page not found: %s", path)
            return {}
        logger.info("Using Rightmove sample page: %s", path)
        html = path.read_text(encoding="utf-8")
        result = _parse_html(html, url)
        _report_missing(result, rid)
        return result

    # 3. Offline mode — fail fast instead of starting Chrome
    if settings.rightmove_scraper_offline:
        logger.warning(
            "No cached Rightmove page for %s and offline mode is enabled. Pre-populate the cache before running tests.",
            rid,
        )
        return {}

    # 4. Normal mode — fetch via Chrome CDP
    await _human_delay()
    html = await _fetch_via_chrome(url)

    if _is_login_wall(html):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(html, encoding="utf-8")
        logger.warning(
            "Rightmove returned a login/verification page for %s. "
            "Please open Chrome in non-headless mode, navigate to "
            "Rightmove and sign in, then try again.",
            url,
        )
        return {"_error": "login_required"}

    if html:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(html, encoding="utf-8")
        logger.info("Cached Rightmove page to %s", cache_file)

    result = _parse_html(html, url) if html else {}
    if result:
        _report_missing(result, rid)
    return result


async def scrape_live(url: str) -> dict[str, Any]:
    """Fetch a Rightmove page via Chrome CDP, cache it, and return parsed data.

    Only call this when the user has explicitly opted in to live Rightmove
    access. Applies randomised back-off and caches the HTML on success.

    If Rightmove returns a login/verification wall, returns
    ``{"_error": "login_required"}``.
    """
    rid = _rid_from_url(url)
    if not rid:
        logger.warning("Could not extract Rightmove ID from URL: %s", url)
        return {}

    await _human_delay()
    html = await _fetch_via_chrome(url)

    if _is_login_wall(html):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = CACHE_DIR / f"{rid}.html"
        cache_file.write_text(html, encoding="utf-8")
        logger.warning(
            "Rightmove returned a login/verification page for %s. "
            "Please open Chrome in non-headless mode, navigate to "
            "Rightmove and sign in, then try again.",
            url,
        )
        return {"_error": "login_required"}

    if html:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = CACHE_DIR / f"{rid}.html"
        cache_file.write_text(html, encoding="utf-8")
        logger.info("Cached Rightmove page to %s", cache_file)

    result = _parse_html(html, url) if html else {}
    if result:
        _report_missing(result, rid)
    return result
