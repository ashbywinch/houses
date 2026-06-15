"""Application-layer DAG sync: seed source values and resolve derived nodes.

This module is the bridge between raw sheet data and the DAG model. It does
NOT know about cards, templates, or display -- it only ensures the DAG has
resolved data for a set of properties.

Routes and card assemblers call this module, not the DAG resolver directly.
"""
from __future__ import annotations

import logging

from houses.geo import GeoPoint
from houses.model.persistence import insert_source_value, insert_user_input, load_property_data
from houses.model.resolver import resolve_property
from houses.web.geo_utils import valid_location

logger = logging.getLogger(__name__)


def seed_dag_from_row(rid: str, row: dict[str, str]) -> bool:
    """Insert source values from a sheet row into the DAG for a property that
    has not been imported yet. Returns True if any values were inserted."""
    imported = False
    address = (row.get("Address") or "").strip()
    postcode = (row.get("Postcode") or "").strip()
    url = (row.get("Rightmove URL") or "").strip()
    bedrooms = (row.get("Bedrooms") or "").strip()
    price = (row.get("Price (£)") or "").strip()
    approx_lat = (row.get("Approx Latitude (est)") or "").strip()
    approx_lng = (row.get("Approx Longitude (est)") or "").strip()

    insert_source_value(rid, "rid", rid, "Derived")
    if url:
        insert_source_value(rid, "rightmove_url", url, "Browser extension")
        imported = True
    if address:
        insert_source_value(rid, "rightmove_address", address, "Rightmove")
        imported = True
    if bedrooms:
        insert_source_value(rid, "rightmove_bedrooms", bedrooms, "Rightmove")
        imported = True
    if price:
        insert_source_value(rid, "rightmove_price", price, "Rightmove")
        imported = True
    if approx_lat and approx_lng:
        try:
            flat, flng = float(approx_lat), float(approx_lng)
            if valid_location(flat, flng, postcode):
                insert_source_value(rid, "rightmove_location", GeoPoint(flat, flng), "Rightmove map")
                imported = True
        except (ValueError, TypeError):
            pass

    actual_lat = (row.get("Actual Latitude") or "").strip()
    actual_lng = (row.get("Actual Longitude") or "").strip()
    if actual_lat and actual_lng:
        try:
            aflat, aflng = float(actual_lat), float(actual_lng)
            if valid_location(aflat, aflng, postcode):
                insert_user_input(rid, "precise_location", GeoPoint(aflat, aflng))
                imported = True
        except (ValueError, TypeError):
            pass
    if address and postcode and postcode not in address:
        try:
            from houses.location import PropertyLocation

            upgraded = PropertyLocation._upgrade_address(address, postcode)
            corrected = upgraded if upgraded != address else f"{address}, {postcode}"
            insert_user_input(rid, "corrected_address", corrected)
            imported = True
        except Exception:
            pass
    return imported


async def sync_property(rid: str, row: dict[str, str] | None = None) -> dict:
    """Ensure a property has resolved DAG data, seeding from the sheet row if needed."""
    data = load_property_data(rid)
    has_location = "best_location" in data.derived
    if not has_location and row is not None:
        seed_dag_from_row(rid, row)
    return await resolve_property(rid, node_ids=["best_address", "best_location"])
