from __future__ import annotations

import logging
from dataclasses import dataclass

from houses.config import settings
from houses.sheets import get_client
from houses.walkability import _extract_town

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
    best_lat: float | None = None
    best_lng: float | None = None
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

    best_lat = _try_float(data.get("Best Latitude", ""))
    best_lng = _try_float(data.get("Best Longitude", ""))

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

    def _dir_url(lat: float | None, lng: float | None, dest: str) -> str:
        if lat is None or lng is None:
            return ""
        return f"https://www.google.com/maps/dir/{lat},{lng}/{dest}"

    simon_dir = _dir_url(best_lat, best_lng, "SW1V+2QQ")
    lorena_dir = _dir_url(best_lat, best_lng, "EC3A+7LP")
    bracknell_dir = _dir_url(best_lat, best_lng, "RG12+8YA")
    town = _extract_town(data.get("Address", ""))
    walk_dir = _dir_url(best_lat, best_lng, town.replace(" ", "+")) if town else ""
    primary_raw = data.get("Primary School", "")
    secondary_raw = data.get("Secondary School", "")
    primary_dir = _dir_url(best_lat, best_lng, _sn(primary_raw).replace(" ", "+")) if primary_raw else ""
    secondary_dir = _dir_url(best_lat, best_lng, _sn(secondary_raw).replace(" ", "+")) if secondary_raw else ""

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
        address=data.get("Address", ""),
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
        best_lat=best_lat,
        best_lng=best_lng,
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


def get_all_cards() -> list[CardData]:
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
        cards.append(_build_card(dr, vr))

    cards.sort(key=lambda c: c.score, reverse=True)
    return cards
