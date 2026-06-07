"""EPC rating lookup via the UK Government Energy Performance Data API.

Base URL: https://api.get-energy-performance-data.communities.gov.uk
Docs: https://get-energy-performance-data.communities.gov.uk/api-documentation/index.html
Auth: Bearer token. Register at the docs page.
"""

from __future__ import annotations

import logging

import httpx

from houses.api_cache import get_cached, set_cached
from houses.config import settings

logger = logging.getLogger(__name__)

EPC_SEARCH_URL = "https://api.get-energy-performance-data.communities.gov.uk/api/domestic/search"


async def lookup_epc(postcode: str) -> str:
    """Look up EPC band for a property postcode.

    Returns the current energy efficiency band (A–G string) or
    empty string if unavailable.
    """
    if not settings.epc_bearer_token:
        return ""

    pc = postcode.strip().upper()
    params = {"postcode": pc, "page_size": 5}

    cached = get_cached("GET", EPC_SEARCH_URL, params)
    if cached is not None:
        certs = cached.get("data", [])
        if not certs:
            return ""
        certs.sort(key=lambda c: c.get("registrationDate", ""), reverse=True)
        band = certs[0].get("currentEnergyEfficiencyBand", "")
        return band.strip() if band else ""

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
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
                return ""

            data = resp.json()
            set_cached("GET", EPC_SEARCH_URL, params, None, data)
            certs = data.get("data", [])
            if not certs:
                return ""

            certs.sort(key=lambda c: c.get("registrationDate", ""), reverse=True)
            band = certs[0].get("currentEnergyEfficiencyBand", "")
            return band.strip() if band else ""

    except Exception as e:
        logger.warning("EPC lookup failed for %s: %s", postcode, e)
        return ""
