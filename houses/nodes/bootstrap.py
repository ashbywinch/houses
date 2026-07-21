from __future__ import annotations

import logging
import re
from typing import Any

from money import Money

from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint
from houses.nodes.property import PropertyNodes
from houses.property_registry import register_property
from houses.sheets.reader import get_properties_data

logger = logging.getLogger(__name__)

SOURCE_LABELS: dict[str, str] = {
    "rightmove_url": "Browser extension",
    "rightmove_address": "Rightmove",
    "rightmove_bedrooms": "Rightmove",
    "rightmove_price": "Rightmove",
    "rightmove_location": "Rightmove map",
    "precise_location": "User location",
    "actual_postcode": "Rightmove",
    "corrected_address": "User correction",
    "user_entered_address": "User correction",
}

COMMENT_LABELS: dict[str, str] = {
    "comment_status": "Sheet",
    "comment_status_reason": "Sheet",
    "comment_group_notes": "Sheet",
    "comment_ashby_comments": "Sheet",
    "comment_ashby_works": "Sheet",
    "comment_design_needed": "Sheet",
    "comment_planning_needed": "Sheet",
}

COMMENT_COLUMNS: dict[str, str] = {
    "comment_status": "Status",
    "comment_status_reason": "Status Reason",
    "comment_group_notes": "Group Notes / WhatsApp",
    "comment_ashby_comments": "Ashby comments",
    "comment_ashby_works": "Ashby Works Estimate",
    "comment_design_needed": "Design Needed",
    "comment_planning_needed": "Planning Needed",
}


_OUTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z0-9]?)\s*\Z", re.IGNORECASE)


def _upgrade_address(address: str, postcode: str) -> str:
    if not address or not postcode:
        return address
    if postcode in address:
        return address
    m = _OUTCODE_RE.search(address)
    if m:
        before = address[: m.start()].rstrip(", ").rstrip()
        return f"{before}, {postcode}"
    return f"{address}, {postcode}"


def bootstrap_from_row(row: dict[str, Any], sources: dict[str, UserInputNode]) -> int:
    pushed = 0

    address = (row.get("Address") or "").strip()
    postcode = (row.get("Postcode") or "").strip()
    url = (row.get("Rightmove URL") or "").strip()
    bedrooms = (row.get("Bedrooms") or "").strip()
    price = (row.get("Price (£)") or "").strip()

    if url and "rightmove_url" in sources:
        sources["rightmove_url"].push(url, SOURCE_LABELS["rightmove_url"])
        pushed += 1

    if address and "rightmove_address" in sources:
        sources["rightmove_address"].push(address, SOURCE_LABELS["rightmove_address"])
        pushed += 1

    if bedrooms and "rightmove_bedrooms" in sources:
        sources["rightmove_bedrooms"].push(bedrooms, SOURCE_LABELS["rightmove_bedrooms"])
        pushed += 1

    if price and "rightmove_price" in sources:
        cleaned = re.sub(r"[^0-9.]", "", price)
        if cleaned:
            sources["rightmove_price"].push(Money(cleaned, "GBP"), SOURCE_LABELS["rightmove_price"])
            pushed += 1

    approx_lat = (row.get("Approx Latitude (est)") or "").strip()
    approx_lng = (row.get("Approx Longitude (est)") or "").strip()
    if approx_lat and approx_lng and "rightmove_location" in sources:
        try:
            flat, flng = float(approx_lat), float(approx_lng)
            sources["rightmove_location"].push(
                GeoPoint(flat, flng),
                SOURCE_LABELS["rightmove_location"],
            )
            pushed += 1
        except (ValueError, TypeError) as exc:
            logger.warning("Invalid approx coords: lat=%s lng=%s (%s)", approx_lat, approx_lng, exc)

    actual_lat = (row.get("Actual Latitude") or "").strip()
    actual_lng = (row.get("Actual Longitude") or "").strip()
    if actual_lat and actual_lng and "precise_location" in sources:
        try:
            aflat, aflng = float(actual_lat), float(actual_lng)
            sources["precise_location"].push(
                GeoPoint(aflat, aflng),
                SOURCE_LABELS["precise_location"],
            )
            pushed += 1
        except (ValueError, TypeError) as exc:
            logger.warning("Invalid actual coords: lat=%s lng=%s (%s)", actual_lat, actual_lng, exc)

    if address and postcode:
        upgraded = _upgrade_address(address, postcode)
        if upgraded != address and "user_entered_address" in sources:
            sources["user_entered_address"].push(
                upgraded,
                SOURCE_LABELS["user_entered_address"],
            )
            pushed += 1
        if "corrected_address" in sources:
            sources["corrected_address"].push(
                upgraded,
                SOURCE_LABELS["corrected_address"],
            )
            pushed += 1

    if postcode and "postcode" in sources:
        sources["postcode"].push(postcode, "Sheet")
        pushed += 1

    actual_postcode = (row.get("Actual Postcode") or "").strip()
    if actual_postcode and "actual_postcode" in sources:
        sources["actual_postcode"].push(actual_postcode, SOURCE_LABELS["actual_postcode"])
        pushed += 1

    for source_key, col_name in COMMENT_COLUMNS.items():
        if source_key not in sources:
            continue
        val = (row.get(col_name) or "").strip()
        if val:
            src = sources[source_key]
            label = COMMENT_LABELS.get(source_key, "Sheet")
            if isinstance(src, UserInputNode) and src._value_type is float:
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    continue
            src.push(val, label)
            pushed += 1

    return pushed


def seed_registry_from_sheet() -> int:
    from dag.persistence import property_rids

    db_rids = property_rids()
    if db_rids:
        for rid in db_rids:
            prop = PropertyNodes(rid)
            register_property(rid, prop)
        logger.info("Rebuilt registry for %d properties from DB", len(db_rids))
        return len(db_rids)

    rows = get_properties_data()
    count = 0
    for row in rows:
        raw_rid = (row.get("Rightmove ID") or "").strip()
        if not raw_rid:
            continue
        prop = PropertyNodes(raw_rid)
        source_dict = {
            "rightmove_address": prop.rightmove_address,
            "rightmove_url": prop.rightmove_url,
            "rightmove_bedrooms": prop.rightmove_bedrooms,
            "rightmove_price": prop.rightmove_price,
            "rightmove_location": prop.rightmove_location,
            "precise_location": prop.precise_location,
            "corrected_address": prop.corrected_address,
            "user_entered_address": prop.user_entered_address,
            "postcode": prop.postcode,
            "comment_status": prop.comment_status,
            "comment_status_reason": prop.comment_status_reason,
            "comment_group_notes": prop.comment_group_notes,
            "comment_ashby_comments": prop.comment_ashby_comments,
            "comment_ashby_works": prop.comment_ashby_works,
            "comment_design_needed": prop.comment_design_needed,
            "comment_planning_needed": prop.comment_planning_needed,
        }
        bootstrap_from_row(row, source_dict)
        register_property(raw_rid, prop)
        count += 1

    logger.info("Seeded %d properties from sheet", count)
    return count
