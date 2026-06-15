from __future__ import annotations

import logging
from dataclasses import dataclass

from houses.config import settings
from houses.geo import GeoPoint
from houses.sheets import get_client
from houses.walkability import _extract_town
from houses.web.geo_utils import valid_location

logger = logging.getLogger(__name__)

VIEW_TAB = "Properties View"


# ── Colour helpers ──────────────────────────────────────────────────────


def commute_colour(minutes: int | None, bracknell: bool = False) -> str:
    if minutes is None:
        return "muted"
    if bracknell:
        return "good" if minutes < 30 else "warn" if minutes <= 60 else "bad"
    return "good" if minutes < 45 else "warn" if minutes <= 75 else "bad"


def ofsted_colour(rating: str) -> str:
    if rating == "Outstanding":
        return "good"
    if rating == "Good":
        return "warn"
    if rating in ("Requires Improvement", "Inadequate"):
        return "bad"
    return "muted"


def walk_colour(minutes: int | None) -> str:
    if minutes is None:
        return "muted"
    return "good" if minutes < 15 else "warn" if minutes <= 30 else "bad"


# ── Value helpers ───────────────────────────────────────────────────────


def _clean_number(val: str) -> str:
    """Strip currency symbols, commas, whitespace."""
    if not val:
        return ""
    return val.replace("£", "").replace(",", "").replace(" ", "").strip()


def _try_float(val: str) -> float | None:
    cleaned = _clean_number(val)
    try:
        return float(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


def _try_int(val: str) -> int | None:
    cleaned = _clean_number(val)
    try:
        return int(float(cleaned)) if cleaned else None
    except (ValueError, TypeError):
        return None


def _postcode_district(postcode: str) -> str:
    if not postcode:
        return ""
    parts = postcode.strip().split()
    if parts:
        outcode = parts[0]
        i = 0
        while i < len(outcode) and outcode[i].isalpha():
            i += 1
        j = i
        while j < len(outcode) and outcode[j].isdigit():
            j += 1
        return outcode[:j]
    return ""


# ── Card data model ─────────────────────────────────────────────────────


@dataclass
class CardData:
    rid: str = ""
    address: str = ""
    price: float | None = None
    bedrooms: int | None = None
    postcode_district: str = ""

    simon_minutes: int | None = None
    simon_dur: str = ""
    simon_cost: float | None = None
    lorena_minutes: int | None = None
    lorena_dur: str = ""
    lorena_cost: float | None = None
    bracknell_minutes: int | None = None
    bracknell_dur: str = ""
    bracknell_cost: float | None = None

    primary_name: str = ""
    primary_ofsted: str = ""
    primary_walk_minutes: int | None = None
    primary_inspection_year: str = ""

    secondary_name: str = ""
    secondary_ofsted: str = ""
    secondary_walk_minutes: int | None = None
    secondary_bus_minutes: int | None = None
    secondary_inspection_year: str = ""

    rightmove_url: str = ""
    map_url: str = ""
    best_location: GeoPoint | None = None
    total_monthly_cost: float | None = None
    walk_to_town_minutes: int | None = None
    town_name: str = ""
    status: str = ""

    primary_url: str = ""
    secondary_url: str = ""

    # Direction links
    walk_dir_url: str = ""
    simon_dir_url: str = ""
    lorena_dir_url: str = ""
    bracknell_dir_url: str = ""
    primary_dir_url: str = ""
    secondary_dir_url: str = ""

    # Computed colours
    simon_colour: str = "muted"
    lorena_colour: str = "muted"
    bracknell_colour: str = "muted"
    primary_ofsted_colour: str = "muted"
    primary_ofsted_label: str = ""
    primary_walk_colour: str = "muted"
    primary_walk_label: str = ""
    secondary_ofsted_colour: str = "muted"
    secondary_ofsted_label: str = ""
    secondary_walk_colour: str = "muted"
    secondary_walk_label: str = ""
    walk_colour: str = "muted"
    score: int = 0
    is_enriched: bool = False


# ── Sheet readers ────────────────────────────────────────────────────────


def get_data_rows() -> list[dict[str, str]]:
    client = get_client()
    if not client:
        return []
    try:
        sh = client.open_by_key(settings.sheet_id)
        ws = sh.worksheet("Properties Data")
        all_rows = ws.get_all_values()
        headers = all_rows[0]
        return [dict(zip(headers, row, strict=False)) for row in all_rows[1:] if row and row[0].strip()]
    except Exception as e:
        logger.warning("Failed to read Data tab: %s", e)
        return []


def get_view_rows() -> list[dict[str, str]]:
    client = get_client()
    if not client:
        return []
    try:
        sh = client.open_by_key(settings.sheet_id)
        ws = sh.worksheet(VIEW_TAB)
        all_rows = ws.get_all_values()
        headers = all_rows[0]
        return [dict(zip(headers, row, strict=False)) for row in all_rows[1:] if row and row[0].strip()]
    except Exception as e:
        logger.warning("Failed to read View tab: %s", e)
        return []


# ── Transform ────────────────────────────────────────────────────────────


# Mapping of postcode area prefixes to lat/lng bounding boxes.
# Covers the postcode areas currently in the system (London + South East).
# Coordinates outside these bounds are likely wrong and rejected.
def _dir_url(loc: GeoPoint | None, dest: str) -> str:
    if loc is None:
        return ""
    return f"https://www.google.com/maps/dir/{loc.lat},{loc.lon}/{dest}"


def _map_url(loc: GeoPoint | None) -> str:
    if loc is None:
        return ""
    return f"https://www.google.com/maps?q={loc.lat},{loc.lon}"


def _set_dir_urls(card: CardData, loc: GeoPoint) -> None:
    card.map_url = _map_url(loc)
    card.simon_dir_url = _dir_url(loc, "SW1V+2QQ")
    card.lorena_dir_url = _dir_url(loc, "EC3A+7LP")
    card.bracknell_dir_url = _dir_url(loc, "RG12+8YA")
    if card.town_name:
        card.walk_dir_url = _dir_url(loc, card.town_name.replace(" ", "+"))
    if card.primary_name:
        sn = card.primary_name.split(",")[0].strip()
        card.primary_dir_url = _dir_url(loc, sn.replace(" ", "+"))
    if card.secondary_name:
        sn = card.secondary_name.split(",")[0].strip()
        card.secondary_dir_url = _dir_url(loc, sn.replace(" ", "+"))


def _card_address(data: dict[str, str]) -> str:
    address = (data.get("Address") or "").strip()
    postcode = (data.get("Postcode") or "").strip()
    if address and postcode and postcode not in address:
        try:
            from houses.location import PropertyLocation
            upgraded = PropertyLocation._upgrade_address(address, postcode)
            return upgraded if upgraded != address else f"{address}, {postcode}"
        except Exception:
            return f"{address}, {postcode}"
    return address or ""


def _build_card(data: dict[str, str], view: dict[str, str]) -> CardData:
    price = _try_float(data.get("Price (£)", ""))
    bedrooms = _try_int(data.get("Bedrooms", ""))
    simon_m = _try_int(data.get("Simon London (min)", ""))
    lorena_m = _try_int(data.get("Lorena London (min)", ""))
    bracknell_m = _try_int(data.get("Bracknell Time (min)", ""))
    simon_c = _try_float(data.get("Simon London Cost (£)", ""))
    lorena_c = _try_float(data.get("Lorena London Cost (£)", ""))
    bracknell_c = _try_float(data.get("Bracknell Cost (£)", ""))
    primary_walk = _try_int(data.get("Primary Walk (min)", ""))
    secondary_walk = _try_int(data.get("Secondary Walk (min)", ""))
    secondary_bus = _try_int(data.get("Secondary Bus (min)", ""))
    walk_town = _try_int(data.get("Walk to Town (min)", ""))
    total_cost = _try_float(view.get("Total Monthly Housing Cost (£)", ""))

    bl = _try_float(data.get("Best Latitude", ""))
    blng = _try_float(data.get("Best Longitude", ""))
    postcode = data.get("Postcode", "")
    best_location = (
        GeoPoint(lat=bl, lon=blng)
        if bl is not None and blng is not None and valid_location(bl, blng, postcode)
        else None
    )

    def _sn(raw: str) -> str:
        return raw.split(",")[0].strip() if raw else ""

    def _dur(m: int | None) -> str:
        if m is None:
            return ""
        if m < 60:
            return f"{m}m"
        h = m // 60
        r = m % 60
        return f"{h}h{r}" if r else f"{h}h"

    card_addr = _card_address(data)
    simon_dir = _dir_url(best_location, "SW1V+2QQ")
    lorena_dir = _dir_url(best_location, "EC3A+7LP")
    bracknell_dir = _dir_url(best_location, "RG12+8YA")
    town = _extract_town(data.get("Address", ""))
    walk_dir = _dir_url(best_location, town.replace(" ", "+")) if town else ""
    primary_raw = data.get("Primary School", "")
    secondary_raw = data.get("Secondary School", "")
    primary_dir = _dir_url(best_location, _sn(primary_raw).replace(" ", "+")) if primary_raw else ""
    secondary_dir = _dir_url(best_location, _sn(secondary_raw).replace(" ", "+")) if secondary_raw else ""

    status = (view.get("Status", "") or "").strip()
    raw_ofsted_p = data.get("Primary Ofsted", "")
    raw_ofsted_s = data.get("Secondary Ofsted", "")

    def first_word(s: str) -> str:
        if not s.strip():
            return ""
        return s.split()[0].rstrip(",.;:!?")

    def walk_label(walk_m: int | None, bus_m: int | None) -> str:
        if bus_m is not None:
            return f"{bus_m}m bus"
        if walk_m is not None:
            return f"{walk_m}m walk"
        return ""

    p_label = first_word(raw_ofsted_p)
    s_label = first_word(raw_ofsted_s)

    is_enriched = simon_m is not None or lorena_m is not None or bracknell_m is not None

    secondary_walk_val = secondary_bus or secondary_walk

    colour_values = [
        commute_colour(simon_m, bracknell=False),
        commute_colour(lorena_m, bracknell=False),
        commute_colour(bracknell_m, bracknell=True),
        ofsted_colour(p_label),
        walk_colour(primary_walk),
        ofsted_colour(s_label),
        walk_colour(secondary_walk_val),
        walk_colour(walk_town),
    ]
    score = sum(2 if c == "good" else 1 if c == "warn" else -1 if c == "bad" else 0 for c in colour_values)

    return CardData(
        rid=data.get("Rightmove ID", ""),
        rightmove_url=data.get("Rightmove URL", ""),
        map_url=data.get("Map URL", ""),
        address=card_addr,
        price=price,
        bedrooms=bedrooms,
        postcode_district=_postcode_district(data.get("Postcode", "")),
        simon_minutes=simon_m,
        simon_dur=_dur(simon_m),
        simon_cost=simon_c,
        lorena_minutes=lorena_m,
        lorena_dur=_dur(lorena_m),
        lorena_cost=lorena_c,
        bracknell_minutes=bracknell_m,
        bracknell_dur=_dur(bracknell_m),
        bracknell_cost=bracknell_c,
        primary_name=_sn(data.get("Primary School", "")),
        primary_ofsted=raw_ofsted_p,
        primary_walk_minutes=primary_walk,
        primary_inspection_year=data.get("Primary Inspection Year", ""),
        secondary_name=_sn(data.get("Secondary School", "")),
        secondary_ofsted=raw_ofsted_s,
        secondary_walk_minutes=secondary_walk,
        secondary_bus_minutes=secondary_bus,
        secondary_inspection_year=data.get("Secondary Inspection Year", ""),
        total_monthly_cost=total_cost,
        walk_to_town_minutes=walk_town,
        town_name=_extract_town(data.get("Address", "")),
        best_location=best_location,
        primary_url=data.get("Primary School Link", ""),
        secondary_url=data.get("Secondary School Link", ""),
        walk_dir_url=walk_dir,
        simon_dir_url=simon_dir,
        lorena_dir_url=lorena_dir,
        bracknell_dir_url=bracknell_dir,
        primary_dir_url=primary_dir,
        secondary_dir_url=secondary_dir,
        status=status,
        simon_colour=commute_colour(simon_m, bracknell=False),
        lorena_colour=commute_colour(lorena_m, bracknell=False),
        bracknell_colour=commute_colour(bracknell_m, bracknell=True),
        primary_ofsted_colour=ofsted_colour(p_label),
        primary_ofsted_label=p_label,
        primary_walk_colour=walk_colour(primary_walk),
        primary_walk_label=walk_label(primary_walk, None),
        secondary_ofsted_colour=ofsted_colour(s_label),
        secondary_ofsted_label=s_label,
        secondary_walk_colour=walk_colour(secondary_walk_val),
        secondary_walk_label=walk_label(secondary_walk, secondary_bus),
        walk_colour=walk_colour(walk_town),
        score=score,
        is_enriched=is_enriched,
    )


def _seed_dag_from_row(rid: str, row: dict[str, str]) -> bool:
    """Insert source values from a sheet row into the DAG for a property that
    hasn't been imported yet. Returns True if any values were inserted."""
    from houses.geo import GeoPoint
    from houses.model.persistence import insert_source_value, insert_user_input

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


def _enrich_from_dag(card: CardData) -> CardData:
    """Check if DAG data exists for this card."""
    if not card.rid:
        return card
    try:
        from houses.model.persistence import load_property_data

        data = load_property_data(card.rid)
    except Exception:
        return card
    if "best_address" in data.derived and data.derived["best_address"].value:
        card.address = data.derived["best_address"].value
    return card


async def get_all_cards() -> list[CardData]:
    data_rows = get_data_rows()
    if not data_rows:
        return []

    view_rows = get_view_rows()
    view_by_rid: dict[str, dict[str, str]] = {}
    for vr in view_rows:
        rid = (vr.get("Rightmove ID", "") or "").strip()
        if rid:
            view_by_rid[rid] = vr

    cards: list[CardData] = []
    for dr in data_rows:
        rid = (dr.get("Rightmove ID", "") or "").strip()
        if not rid:
            continue
        vr = view_by_rid.get(rid, {})
        card = _build_card(dr, vr)
        cards.append(_enrich_from_dag(card))

    for card in cards:
        if not card.rid:
            continue
        try:
            from houses.model.persistence import load_property_data

            data = load_property_data(card.rid)
            has_location = "best_location" in data.derived
        except Exception:
            has_location = False

        if not has_location:
            dr = next((r for r in data_rows if (r.get("Rightmove ID") or "").strip() == card.rid), None)
            if dr:
                _seed_dag_from_row(card.rid, dr)

        try:
            from houses.model.resolver import resolve_property

            results = await resolve_property(card.rid, node_ids=["best_address", "best_location"])
        except Exception:
            continue

        bl = results.get("best_location")
        if bl and bl.value and isinstance(bl.value, GeoPoint):
            if not valid_location(bl.value.lat, bl.value.lon, card.postcode_district):
                card.best_location = None
            else:
                card.best_location = bl.value
                _set_dir_urls(card, bl.value)

        ba = results.get("best_address")
        if ba and ba.value:
            card.address = ba.value

    cards.sort(key=lambda c: c.score, reverse=True)
    return cards
