"""Council tax band lookup via Homedata API + CivAccount rates."""

from __future__ import annotations

import logging
import re

import httpx

from houses.config import settings
from houses.models import CouncilTaxInfo

logger = logging.getLogger(__name__)

# Homedata free tier returns 404 for all property lookups (confirmed).
# This flag is set once per process at first call and never re-tried.
# Upgrade to a paid plan to re-enable: set _homedata_disabled = False
_homedata_disabled = True
logger.info("Homedata: disabled (free tier doesn't return property data)")
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
HOMEDATA_URL = "https://homedata.co.uk/api/council_tax_band/"
CIVACCOUNT_URL = "https://www.civaccount.co.uk/api/v1/councils"


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
    # Check if first part starts with a number (building number)
    num_match = re.match(r"^(\d+[A-Z]?)\s", building)
    if num_match:
        return {"postcode": postcode, "building_number": num_match.group(1)}
    return {"postcode": postcode, "building_name": building}


async def lookup_council_tax(postcode: str, address: str = "") -> CouncilTaxInfo | None:
    if not settings.homedata_api_key:
        logger.warning("Homedata API key not configured")
        return None

    params = _extract_building(address) if address else {"postcode": postcode}

    if not params.get("postcode"):
        logger.warning("No valid postcode for council tax lookup")
        return None

    # Homedata free tier doesn't return property data — skip if proven
    global _homedata_disabled
    if _homedata_disabled:
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                HOMEDATA_URL,
                params=params,
                headers={"Authorization": f"Api-Key {settings.homedata_api_key}"},
            )
            if resp.status_code == 404:
                logger.warning("Council tax lookup failed — Homedata free tier has no property data. Disabling.")
                _homedata_disabled = True
                return None
            if resp.status_code in (400, 422):
                logger.warning("Homedata bad request for %s: %s", params, resp.text)
                return None
            if resp.status_code == 403:
                logger.warning("Homedata API key lacks permission for %s: %s", params, resp.text)
                return None
            resp.raise_for_status()
            data = resp.json()

        band = data.get("council_tax_band", "")
        local_authority = data.get("local_authority", "")

        if not band:
            return CouncilTaxInfo(band="", yearly_cost=None, evidence_url="")

        # Derive council slug for CivAccount
        slug = local_authority.lower().replace(" ", "-").replace(".", "")
        evidence_url = f"https://www.civaccount.co.uk/councils/{slug}"

        # Fetch Band D rate from CivAccount
        yearly_cost = None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client2:
                civ = await client2.get(f"{CIVACCOUNT_URL}/{slug}")
                if civ.status_code == 200:
                    civ_data = civ.json()
                    band_d_rate = civ_data.get("band_d_rate")
                    if band_d_rate and band in BAND_RATIOS:
                        yearly_cost = round(band_d_rate * BAND_RATIOS[band], 2)
        except Exception:
            logger.warning("CivAccount lookup failed for %s", slug)

        return CouncilTaxInfo(
            band=band,
            yearly_cost=yearly_cost,
            evidence_url=evidence_url,
        )

    except httpx.HTTPStatusError as e:
        logger.warning("Homedata API error: %s", e)
        return None
    except Exception as e:
        logger.warning("Council tax lookup failed: %s", e)
        return None
