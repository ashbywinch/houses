"""EPC rating lookup via the UK Government Energy Performance Data API.

Base URL: https://api.get-energy-performance-data.communities.gov.uk
Docs: https://get-energy-performance-data.communities.gov.uk/api-documentation/index.html
Auth: Bearer token. Register at the docs page.
"""

from __future__ import annotations

import logging
import re

import httpx

from dag.attempt import Attempt
from houses.api_cache import cached_async_client, get_cached, set_cached
from houses.config import settings

logger = logging.getLogger(__name__)

EPC_SEARCH_URL = "https://api.get-energy-performance-data.communities.gov.uk/api/domestic/search"

ROAD_SUFFIXES = frozenset(
    {
        "road",
        "rd",
        "street",
        "st",
        "lane",
        "drive",
        "dr",
        "close",
        "cl",
        "way",
        "avenue",
        "ave",
        "gardens",
        "gdns",
        "crescent",
        "cres",
        "grove",
        "gr",
        "court",
        "ct",
        "place",
        "pl",
        "square",
        "sq",
        "hill",
        "rise",
        "row",
        "walk",
        "park",
        "meadow",
        "terrace",
        "parade",
        "view",
        "vale",
        "gate",
        "croft",
        "dene",
        "wood",
        "woods",
        "heath",
        "holt",
        "lea",
        "meadows",
    }
)


def _is_road_name(first_token: str) -> bool:
    """Check if the first address token is a road name (ends with road suffix as a separate word)."""
    lower = first_token.strip().lower()
    return any(lower.endswith(f" {s}") for s in ROAD_SUFFIXES)


def _normalise(text: str) -> str:
    return re.sub(r"[^A-Z0-9 ]", "", text.upper().strip())


async def lookup_epc(postcode: str, address: str = "") -> Attempt[str]:
    """Look up EPC band for a property.

    Returns ``Attempt.succeeded(band)`` with the current energy efficiency
    band (A–G string), or ``Attempt.impossible(reason)`` when unavailable —
    e.g. ``"address matched multiple properties"``, ``"no matching
    certificate for this address"``, or ``"no certificates found"``.

    When ``address`` is provided, filters the API results to match
    the building identifier (number or name) against ``addressLine1``
    in each certificate. The specific reason from ``_match_cert`` is
    carried in the Attempt so the frontend can show it.
    """
    if not settings.epc_bearer_token:
        return Attempt.impossible("EPC lookup not configured")

    proceed, building_id = _should_lookup_epc(address)
    if address and not proceed:
        return Attempt.impossible("address has no building identifier")

    pc = postcode.strip().upper()
    params = {"postcode": pc, "page_size": 50}

    cached = get_cached("GET", EPC_SEARCH_URL, params)
    if cached is not None:
        certs = cached.get("data", [])
        return _match_cert(certs, building_id)

    try:
        async with cached_async_client(timeout=10.0) as client:
            resp = await client.get(
                EPC_SEARCH_URL,
                params=params,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {settings.epc_bearer_token}",
                },
            )
            if resp.status_code != 200:
                logger.warning("EPC API returned %d for %s", resp.status_code, postcode)
                return Attempt.impossible(f"EPC API returned status {resp.status_code}")

            data = resp.json()
            set_cached("GET", EPC_SEARCH_URL, params, None, data)
            certs = data.get("data", [])
            return _match_cert(certs, building_id)

    except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException):
        raise  # transient — let DAG retry handle it
    except Exception as e:
        logger.warning("EPC lookup failed for %s: %s", postcode, e)
        return Attempt.impossible(f"EPC lookup failed: {e}")


def _extract_building_id(first_token: str) -> str:
    """Extract building identifier from the first address token."""
    first = first_token.strip()
    m = re.match(r"(\d+)", first)
    if m:
        return m.group(1)
    return first


def _should_lookup_epc(address: str) -> tuple[bool, str]:
    """Decide whether to call the EPC API for this address.

    Returns (proceed, building_id).
    """
    if not address:
        return True, ""

    parts = [p.strip() for p in address.split(",")]
    first = parts[0].strip()

    # 1. Numbered property → proceed
    if first and first[0].isdigit():
        return True, _extract_building_id(first)

    # 2. First token is a road name → skip
    if _is_road_name(first):
        return False, ""

    # 3. Likely a named building → proceed
    return True, _extract_building_id(first)


def _match_cert(certs: list[dict], building_id: str) -> Attempt[str]:
    """Find the most recent certificate, optionally matching the building identifier.

    When *building_id* is provided, returns the band from the most recent
    certificate if **all** matching candidates are for the **same** address.
    If multiple different addresses match (ambiguous — e.g. "High Street"
    could be any of several buildings), returns a failed Attempt with a
    descriptive reason.
    """
    if not certs:
        return Attempt.impossible("no certificates found")

    candidates = certs
    if building_id:
        norm_id = _normalise(building_id)
        candidates = [c for c in certs if norm_id in _normalise(c.get("addressLine1", ""))]
        if not candidates:
            return Attempt.impossible("no matching certificate for this address")
        # Ambiguity check: more than one distinct address matches
        unique_addresses = {c.get("addressLine1", "") for c in candidates}
        if len(unique_addresses) > 1:
            return Attempt.impossible("address matched multiple properties")

    candidates.sort(key=lambda c: c.get("registrationDate", ""), reverse=True)
    band = candidates[0].get("currentEnergyEfficiencyBand", "")
    raw = band.strip() if band else ""
    if not raw:
        return Attempt.impossible("certificate has no energy band")
    return Attempt.succeeded(raw)
