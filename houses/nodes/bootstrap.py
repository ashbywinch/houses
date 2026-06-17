"""Bootstrap the new DAG by pushing sheet-column values to SourceNodes.

This runs alongside the existing seed_dag_from_row (old DAG).
Call bootstrap_from_row() after reading a row from the sheet to populate
the new dag/ library's SourceNodes with user-owned column values.

Dual-path compatibility: this module is ADDED alongside existing code.
Old enrichment modules, sheet writes, and houses/sheets/ are never modified.
"""

from __future__ import annotations

import logging
from typing import Any

from dag.attempt import Provenance
from dag.source_node import SourceNode
from houses.geo import GeoPoint
from houses.web.geo_utils import valid_location

logger = logging.getLogger(__name__)

PROVENANCE_LABELS: dict[str, str] = {
    "rightmove_url": "Browser extension",
    "rightmove_address": "Rightmove",
    "rightmove_bedrooms": "Rightmove",
    "rightmove_price": "Rightmove",
    "rightmove_location": "Rightmove map",
    "precise_location": "User location",
    "corrected_address": "User correction",
}


def _upgrade_address(address: str, postcode: str) -> str:
    """Append postcode to address if not already present."""
    if not address or not postcode:
        return address
    if postcode in address:
        return address
    return f"{address}, {postcode}"


def bootstrap_from_row(row: dict[str, Any],
                       sources: dict[str, SourceNode]) -> int:
    """Push user-owned column values from a sheet row to SourceNodes.

    Args:
        row: Sheet row dict keyed by column name (e.g. "Address", "Postcode").
        sources: Dict mapping node_id to SourceNode[T] instance.

    Returns:
        Number of sources that were pushed.
    """
    pushed = 0

    address = (row.get("Address") or "").strip()
    postcode = (row.get("Postcode") or "").strip()
    url = (row.get("Rightmove URL") or "").strip()
    bedrooms = (row.get("Bedrooms") or "").strip()
    price = (row.get("Price (£)") or "").strip()

    if url and "rightmove_url" in sources:
        sources["rightmove_url"].push(url, Provenance(PROVENANCE_LABELS["rightmove_url"]))
        pushed += 1

    if address and "rightmove_address" in sources:
        sources["rightmove_address"].push(address, Provenance(PROVENANCE_LABELS["rightmove_address"]))
        pushed += 1

    if bedrooms and "rightmove_bedrooms" in sources:
        sources["rightmove_bedrooms"].push(bedrooms, Provenance(PROVENANCE_LABELS["rightmove_bedrooms"]))
        pushed += 1

    if price and "rightmove_price" in sources:
        sources["rightmove_price"].push(price, Provenance(PROVENANCE_LABELS["rightmove_price"]))
        pushed += 1

    approx_lat = (row.get("Approx Latitude (est)") or "").strip()
    approx_lng = (row.get("Approx Longitude (est)") or "").strip()
    if approx_lat and approx_lng and "rightmove_location" in sources:
        try:
            flat, flng = float(approx_lat), float(approx_lng)
            if valid_location(flat, flng, postcode):
                sources["rightmove_location"].push(
                    GeoPoint(flat, flng),
                    Provenance(PROVENANCE_LABELS["rightmove_location"]),
                )
                pushed += 1
        except (ValueError, TypeError) as exc:
            logger.warning("Invalid approx coords: lat=%s lng=%s (%s)", approx_lat, approx_lng, exc)

    actual_lat = (row.get("Actual Latitude") or "").strip()
    actual_lng = (row.get("Actual Longitude") or "").strip()
    if actual_lat and actual_lng and "precise_location" in sources:
        try:
            aflat, aflng = float(actual_lat), float(actual_lng)
            if valid_location(aflat, aflng, postcode):
                sources["precise_location"].push(
                    GeoPoint(aflat, aflng),
                    Provenance(PROVENANCE_LABELS["precise_location"]),
                )
                pushed += 1
        except (ValueError, TypeError) as exc:
            logger.warning("Invalid actual coords: lat=%s lng=%s (%s)", actual_lat, actual_lng, exc)

    if address and postcode and "corrected_address" in sources:
        upgraded = _upgrade_address(address, postcode)
        sources["corrected_address"].push(
            upgraded,
            Provenance(PROVENANCE_LABELS["corrected_address"]),
        )
        pushed += 1

    return pushed
