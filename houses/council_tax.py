"""Council tax band lookup via VOA website scraper + CivAccount rates."""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

import httpx

from houses.api_cache import get_cached, set_cached
from houses.models import CouncilTaxInfo

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
    """Fetch the Band D rate from CivAccount, falling back to the CSV."""
    # 1) Try CivAccount (live API, may have up-to-date rates)
    slug = local_authority.lower().replace(" ", "-").replace(".", "")
    url = f"{CIVACCOUNT_URL}/{slug}"
    cached = get_cached("GET", url)
    if cached is not None:
        band_d_rate = cached.get("band_d_rate")
        if band_d_rate and band in BAND_RATIOS:
            return round(band_d_rate * BAND_RATIOS[band], 2)
        return None
    try:
        with httpx.Client(timeout=10.0) as client:
            civ = client.get(url)
            if civ.status_code == 200:
                civ_data = civ.json()
                set_cached("GET", url, None, None, civ_data)
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
    if band_d_rate and band in BAND_RATIOS:
        return round(band_d_rate * BAND_RATIOS[band], 2)

    return None


async def lookup_council_tax(postcode: str, address: str = "") -> CouncilTaxInfo | None:
    """Look up council tax band via VOA website scraper.

    Scrapes the public gov.uk council tax bands page for the given postcode,
    matches the specific property by building name/number, then fetches the
    yearly cost from CivAccount.

    Returns ``CouncilTaxInfo`` with band, yearly cost, and an evidence URL,
    or ``None`` if the lookup fails entirely.
    """
    voa_key = f"voa/{postcode.strip().upper()}"
    voa_cached = get_cached("GET", voa_key)
    if voa_cached is not None:
        results_raw = voa_cached.get("rows", [])
    else:
        try:
            from uk_property_apis.voa import VOAClient

            async with VOAClient() as client:
                page = await client.fetch_page(postcode, page=0)
            results_raw = [
                {"address": r.address, "band": r.band, "local_authority": r.local_authority}
                for r in page.rows
            ]
            set_cached("GET", voa_key, None, None, {"rows": results_raw})
        except ImportError:
            logger.warning("uk-property-apis not installed; skipping council tax lookup")
            return None
        except Exception as e:
            logger.warning("VOA council tax lookup failed for %s: %s", postcode, e)
            return None

    if not address:
        logger.debug("No address provided — cannot positively identify property")
        return None

    active = [r for r in results_raw if r["band"] in BAND_RATIOS or r["band"] == "I"]
    if not active:
        logger.debug("VOA returned no active properties for %s", postcode)
        return None

    building = _extract_building(address)
    building_id = building.get("building_number") or building.get("building_name") or ""
    norm_id = _normalise(building_id)

    if not norm_id:
        logger.debug("Could not extract building identifier from address %r", address)
        return None

    matched = None
    for r in active:
        if norm_id in _normalise(r["address"]):
            matched = r
            break

    if matched is None:
        logger.debug(
            "Could not match building %r in VOA results for %s",
            building_id,
            postcode,
        )
        return None

    yearly_cost = None
    evidence_url = ""
    if matched["local_authority"]:
        yearly_cost = _lookup_yearly_cost(matched["band"], matched["local_authority"])
        slug = matched["local_authority"].lower().replace(" ", "-").replace(".", "")
        evidence_url = f"https://www.civaccount.co.uk/councils/{slug}"
    else:
        logger.warning("No local authority found for %s postcode %s", building_id, postcode)

    return CouncilTaxInfo(
        band=matched["band"],
        yearly_cost=yearly_cost,
        evidence_url=evidence_url,
    )
