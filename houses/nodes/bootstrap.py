from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from money import Money

from dag.persistence import property_rids
from dag.user_input_node import UserInputNode
from houses.geopoint import GeoPoint
from houses.location import upgrade_address
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property

logger = logging.getLogger(__name__)

SOURCE_LABELS: dict[str, str] = {
    "rightmove_url": "Browser extension",
    "rightmove_address": "Rightmove",
    "rightmove_bedrooms": "Rightmove",
    "rightmove_price": "Rightmove",
    "rightmove_location": "Rightmove map",
    "precise_location": "User location",
    "corrected_address": "User correction",
    "user_entered_address": "User correction",
}

COMMENT_LABELS: dict[str, str] = {
    "comment_status": "Sheet",
    "comment_status_reason": "Sheet",
    "comment_group_notes": "Sheet",
    "comment_ashby_comments": "Sheet",
    "comment_design_needed": "Sheet",
    "comment_planning_needed": "Sheet",
}

COMMENT_COLUMNS: dict[str, str] = {
    "comment_status": "Status",
    "comment_status_reason": "Status Reason",
    "comment_group_notes": "Group Notes / WhatsApp",
    "comment_ashby_comments": "Ashby comments",
    "comment_design_needed": "Design Needed",
    "comment_planning_needed": "Planning Needed",
}


def _push_geo_coords(sources: dict[str, UserInputNode], key: str, lat: str, lng: str, what: str) -> bool:
    """Push a sheet coordinate pair into sources; False when the cells aren't numeric."""
    try:
        flat, flng = float(lat), float(lng)
        sources[key].push(GeoPoint(flat, flng), SOURCE_LABELS[key])
        return True
    except (ValueError, TypeError) as exc:
        logger.warning("Invalid %s coords: lat=%s lng=%s (%s)", what, lat, lng, exc)
        return False

def _push_cell(
    sources: dict[str, UserInputNode],
    row: dict[str, str],
    col_name: str,
    source_key: str,
    *,
    parse: Callable[[str], object] | None = None,
) -> bool:
    """Push one stripped sheet cell into a source node; False (no push)
    when the node isn't wired, the cell is blank, or the parser rejects
    it — callers count only real pushes.  The label comes from
    SOURCE_LABELS (Sheet fallback), matching the per-key push chain."""
    if source_key not in sources:
        return False
    val = (row.get(col_name) or "").strip()
    if not val:
        return False
    if parse is not None:
        val = parse(val)
        if val is None:
            return False
    sources[source_key].push(val, SOURCE_LABELS.get(source_key, "Sheet"))
    return True


def _parse_price(value: str) -> Money | None:
    """'£450,000' / '450000' → Money; None when nothing numeric remains."""
    cleaned = re.sub(r"[^0-9.]", "", value)
    if not cleaned:
        return None
    return Money(cleaned, "GBP")

def _push_upgraded_address(sources: dict[str, UserInputNode], row: dict[str, str]) -> int:
    """Push the postcode-upgraded address onto both address nodes; 0–2 pushes."""
    address = (row.get("Address") or "").strip()
    postcode = (row.get("Postcode") or "").strip()
    if not address or not postcode:
        return 0
    upgraded = upgrade_address(address, postcode)
    pushed = 0
    if upgraded != address and "user_entered_address" in sources:
        sources["user_entered_address"].push(upgraded, SOURCE_LABELS["user_entered_address"])
        pushed += 1
    if "corrected_address" in sources:
        sources["corrected_address"].push(upgraded, SOURCE_LABELS["corrected_address"])
        pushed += 1
    return pushed


def _push_comment_cells(sources: dict[str, UserInputNode], row: dict[str, str]) -> int:
    """Push every non-blank comment column; floats coerced for float nodes."""
    pushed = 0
    for source_key, col_name in COMMENT_COLUMNS.items():
        if source_key not in sources:
            continue
        val = (row.get(col_name) or "").strip()
        if not val:
            continue
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


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def bootstrap_from_row(row: dict[str, Any], sources: dict[str, UserInputNode]) -> int:
    pushed = 0

    pushed += _push_cell(sources, row, col_name="Rightmove URL", source_key="rightmove_url")
    pushed += _push_cell(sources, row, col_name="Address", source_key="rightmove_address")
    pushed += _push_cell(sources, row, col_name="Bedrooms", source_key="rightmove_bedrooms")
    pushed += _push_cell(sources, row, col_name="Price (£)", source_key="rightmove_price", parse=_parse_price)

    approx_lat = (row.get("Approx Latitude (est)") or "").strip()
    approx_lng = (row.get("Approx Longitude (est)") or "").strip()
    if approx_lat and approx_lng and "rightmove_location" in sources and _push_geo_coords(
        sources, key="rightmove_location", lat=approx_lat, lng=approx_lng, what="approx"
    ):
        pushed += 1

    actual_lat = (row.get("Actual Latitude") or "").strip()
    actual_lng = (row.get("Actual Longitude") or "").strip()
    if actual_lat and actual_lng and "precise_location" in sources and _push_geo_coords(
        sources, key="precise_location", lat=actual_lat, lng=actual_lng, what="actual"
    ):
        pushed += 1

    pushed += _push_upgraded_address(sources, row)
    pushed += _push_cell(sources, row, col_name="Postcode", source_key="postcode")
    pushed += _push_comment_cells(sources, row)
    return pushed




def _seed_input_defaults(prop) -> None:
    """Materialise defaults for input nodes that are still pending.

    A pending input node with no producer permanently blocks every
    downstream refresh: ``refresh()`` waits for pending deps, so an empty
    sheet "Status" cell (or a DB row that was never written) freezes the
    whole money cascade — equity → mortgage → monthly payment — forever.
    Defaults match the sheet path's semantics: empty status = not
    "Current", no works estimates = {}, no rental income = £0.  Never
    overwrite a value the user (or a source) already set.
    """
    if prop.comment_status.latest_attempt().pending:
        prop.comment_status.push("", "default")
    if prop.comment_status_reason.latest_attempt().pending:
        prop.comment_status_reason.push("", "default")
    if prop.works_estimates.latest_attempt().pending:
        prop.works_estimates.push({}, "default")
    if prop.rental_income.latest_attempt().pending:
        prop.rental_income.push(Money(amount="0", currency="GBP"), "default")


def load_property_nodes_from_db() -> int:
    """Create PropertyNodes for every RID found in the DB.
    Called on normal startup. No sheet dependency.
    Nodes load their persisted values from the database automatically.
    """

    count = 0
    for rid in property_rids():
        prop = PropertyNodes(rid)
        _seed_input_defaults(prop)
        register_property(rid, prop)
        count += 1
    logger.info("Loaded %d properties from DB", count)
    return count

