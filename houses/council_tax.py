"""Council tax band lookup via VOA website scraper + CivAccount rates."""

from __future__ import annotations

import csv
import logging
import re
from collections import namedtuple
from pathlib import Path

from houses.api_cache import cached_sync_client, get_cached, set_cached
from houses.attempt import Attempt
from houses.property import CouncilTaxInfo

logger = logging.getLogger(__name__)

BAND_RATIOS = {
    "A": 6 / 9,
    "B": 7 / 9,
    "C": 8 / 9,
    "D": 9 / 9,
    "E": 11 / 9,
    "F": 13 / 9,
    "G": 15 / 9,
    "H": 18 / 9,
}
CIVACCOUNT_URL = "https://www.civaccount.co.uk/api/v1/councils"
COUNCIL_TAX_CSV = "data/council_tax_rates.csv"

# In-memory cache: lowercased authority name -> Band D rate (float)
_cached_rates: dict[str, float] | None = None


def _load_rates() -> dict[str, float]:
    global _cached_rates
    if _cached_rates is not None:
        return _cached_rates
    _cached_rates = {}
    path = Path(__file__).parent.parent / COUNCIL_TAX_CSV
    if not path.is_file():
        logger.warning("Council tax rates CSV not found at %s", path)
        return _cached_rates
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rate = row.get("band_d_rate", "")
            if rate:
                _cached_rates[row["authority"].strip().lower()] = float(rate)
    logger.debug("Loaded %d council tax rates from %s", len(_cached_rates), COUNCIL_TAX_CSV)
    return _cached_rates


def _extract_building(address: str) -> dict:
    """Extract building name/number and postcode from an address."""
    parts = [p.strip() for p in address.split(",")]
    first = parts[0] if parts else ""
    last = parts[-1] if parts else ""
    # Check if last part looks like a postcode
    pc_match = re.search(r"[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}", last, re.IGNORECASE)
    postcode = pc_match.group(0) if pc_match else ""
    if not postcode:
        outcode_match = re.search(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$", last, re.IGNORECASE)
        postcode = last if outcode_match else ""

    building = first
    num_match = re.match(r"^(\d+[A-Z]?)\s", building)
    if num_match:
        return {"postcode": postcode, "building_number": num_match.group(1)}
    return {"postcode": postcode, "building_name": building}


def _normalise(text: str) -> str:
    """Strip whitespace, uppercase, remove punctuation for comparison."""
    return re.sub(r"[^A-Z0-9 ]", "", text.upper().strip())


def _lookup_yearly_cost(band: str, local_authority: str) -> float | None:
    """Fetch the Band D rate from CivAccount, falling back to the CSV.

    CivAccount is called via ``cached_sync_client`` which automatically
    caches every response to disk — no manual ``get_cached``/``set_cached``
    needed.
    """
    slug = local_authority.lower().replace(" ", "-").replace(".", "")
    url = f"{CIVACCOUNT_URL}/{slug}"
    try:
        with cached_sync_client(timeout=10.0) as client:
            civ = client.get(url)
            if civ.status_code == 200:
                civ_data = civ.json()
                band_d_rate = civ_data.get("band_d_rate")
                if band_d_rate and band in BAND_RATIOS:
                    return round(band_d_rate * BAND_RATIOS[band], 2)
    except Exception:
        logger.warning("CivAccount lookup failed for %s (%s)", local_authority, slug)

    # 2) Fall back to the cached CSV of government Band D rates
    rates = _load_rates()
    norm = local_authority.strip().lower()
    # Try exact match first, then prefix match (e.g. "Woking" matches "Woking")
    band_d_rate = rates.get(norm)
    if band_d_rate is None:
        for key, val in rates.items():
            if key.startswith(norm) or norm.startswith(key):
                band_d_rate = val
                break

    # 3) London boroughs: the CSV only has an aggregate "London boroughs" entry.
    #    Individual borough names (Ealing, Westminster, etc.) don't appear.
    if band_d_rate is None:
        _london_boroughs = frozenset(
            {
                "barking and dagenham",
                "barnet",
                "bexley",
                "brent",
                "bromley",
                "camden",
                "croydon",
                "ealing",
                "enfield",
                "greenwich",
                "hackney",
                "hammersmith and fulham",
                "haringey",
                "harrow",
                "havering",
                "hillingdon",
                "hounslow",
                "islington",
                "kensington and chelsea",
                "kingston upon thames",
                "lambeth",
                "lewisham",
                "merton",
                "newham",
                "redbridge",
                "richmond upon thames",
                "southwark",
                "sutton",
                "tower hamlets",
                "waltham forest",
                "wandsworth",
                "westminster",
                "city of london",
                "city of westminster",
            }
        )
        if norm in _london_boroughs or norm.replace(" ", "") in _london_boroughs:
            for key, val in rates.items():
                if "london borough" in key.lower():
                    band_d_rate = val
                    break

    if band_d_rate and band in BAND_RATIOS:
        return round(band_d_rate * BAND_RATIOS[band], 2)

    return None


class CachedVOAClient:
    """Async context manager that wraps ``VOAClient`` with disk caching.

    ``VOAClient`` has its own internal HTTP client that bypasses our
    ``CachingTransport``, so caching must be added at the ``fetch_page``
    level.  This wrapper provides the same async context manager interface
    as ``VOAClient`` and automatically serializes/restores page results
    to/from the ``data/api_cache/`` disk cache.
    """

    _VoaRow = namedtuple("_VoaRow", ["band", "address", "postcode", "local_authority"])

    def __init__(self):
        self._inner: object | None = None

    async def __aenter__(self):
        from uk_property_apis.voa import VOAClient

        self._inner = VOAClient()
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *args):
        if self._inner is not None:
            await self._inner.__aexit__(*args)

    async def fetch_page(self, postcode: str, page: int = 0):
        key = f"voa/{postcode.strip().upper()}"
        cached = get_cached("GET", key)
        if cached is not None:
            rows = []
            for r in cached.get("rows", []):
                r.setdefault("postcode", "")
                r.setdefault("local_authority", "")
                rows.append(self._VoaRow(**r))
            return type("Page", (), {"rows": rows})()

        result = await self._inner.fetch_page(postcode, page=page)
        rows = [
            {"band": r.band, "address": r.address, "postcode": r.postcode, "local_authority": r.local_authority}
            for r in result.rows
        ]
        set_cached("GET", key, None, None, {"rows": rows})
        return result


async def lookup_council_tax(postcode: str, address: str = "") -> Attempt[CouncilTaxInfo]:
    """Look up council tax band via VOA website scraper.

    Returns an ``Attempt[CouncilTaxInfo]``. When the address is ambiguous
    (matches multiple properties) or no identifier can be extracted, the
    attempt carries the reason (e.g. ``"address matched multiple properties"``).

    Server callers extract the value with ``.value_or_none()`` and store
    ``""`` on the EnrichedProperty for failures.
    """
    try:
        async with CachedVOAClient() as client:
            page = await client.fetch_page(postcode)
        results_raw = [{"address": r.address, "band": r.band, "local_authority": r.local_authority} for r in page.rows]
    except ImportError:
        logger.warning("uk-property-apis not installed; skipping council tax lookup")
        return Attempt.impossible("voa", "uk-property-apis not installed")
    except Exception as e:
        logger.warning("VOA council tax lookup failed for %s: %s", postcode, e)
        return Attempt.impossible("voa", f"VOA lookup failed: {e}")

    if not address:
        logger.debug("No address provided — cannot positively identify property")
        return Attempt.impossible("voa", "no address provided")

    active = [r for r in results_raw if r["band"] in BAND_RATIOS or r["band"] == "I"]
    if not active:
        logger.debug("VOA returned no active properties for %s", postcode)
        return Attempt.impossible("voa", "no active properties in VOA results")

    building = _extract_building(address)
    building_id = building.get("building_number") or building.get("building_name") or ""
    norm_id = _normalise(building_id)

    if not norm_id:
        logger.debug("Could not extract building identifier from address %r", address)
        return Attempt.impossible("voa", "could not extract building identifier")

    matches = [r for r in active if norm_id in _normalise(r["address"])]

    if not matches:
        logger.debug("Could not match building %r in VOA results for %s", building_id, postcode)
        return Attempt.impossible("voa", f"no VOA match for building {building_id}")

    # Ambiguity check: more than one distinct address matches
    unique_addresses = {m["address"] for m in matches}
    if len(unique_addresses) > 1:
        logger.debug(
            "Ambiguous address %r — matched %d different VOA addresses for %s",
            building_id,
            len(unique_addresses),
            postcode,
        )
        return Attempt.impossible("voa", "address matched multiple properties")

    matched = matches[0]
    yearly_cost = None
    evidence_url = ""
    if matched["local_authority"]:
        yearly_cost = _lookup_yearly_cost(matched["band"], matched["local_authority"])
        slug = matched["local_authority"].lower().replace(" ", "-").replace(".", "")
        evidence_url = f"https://www.civaccount.co.uk/councils/{slug}"
    else:
        logger.warning("No local authority found for %s postcode %s", building_id, postcode)

    return Attempt.succeeded(
        CouncilTaxInfo(band=matched["band"], yearly_cost=yearly_cost, evidence_url=evidence_url),
        "voa",
    )
