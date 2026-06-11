"""FastAPI app — /inject-property endpoint, startup/shutdown."""

import json
import logging
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, StreamingResponse

from houses.commute import Commute, CommuteBreakdown
from houses.config import settings
from houses.council_tax import lookup_council_tax
from houses.enricher import (
    compute_commute_breakdown,
    compute_lorena_commute,
    compute_petrol_cost,
    compute_simon_commute,
)
from houses.epc import lookup_epc
from houses.location import PropertyLocation, geocode
from houses.property import EnrichedProperty, Property
from houses.rail_fares import fare_between, nearest_station
from houses.rightmove_scraper import scrape as scrape_rightmove
from houses.rightmove_scraper import stop_chrome
from houses.schools import SchoolGender, compute_school_commute, find_nearest
from houses.sheets import (
    Tab,
    _rightmove_id,
    col_index,
    col_letter,
    get_client,
    row_values,
    sync_view_formulas,
    write_enriched_row,
)
from houses.town_desc import generate_town_description
from houses.walkability import KNOWN_COUNTIES, enrich_walkability

logger = logging.getLogger(__name__)


def _asdict_serializable(obj: Any) -> Any:
    """Recursively convert a dataclass tree to JSON-serializable dicts.

    Like ``dataclasses.asdict()`` but also converts enums to their values.
    """
    import dataclasses
    from enum import Enum

    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj):
        return {f.name: _asdict_serializable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: _asdict_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_asdict_serializable(v) for v in obj]
    return obj


# UK postcode patterns
# Full: "RG14 1AA", "SW1A 1AA", "EC3A 7LP"
# Outcode (partial): "RG14", "SW1A", "SL6"
_FULL_POSTCODE_RE = re.compile(
    r"[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}",
    re.IGNORECASE,
)
_OUTCODE_RE = re.compile(
    r"\b[A-Z]{1,2}[0-9][A-Z0-9]?\b",
    re.IGNORECASE,
)


def extract_postcode(address: str) -> str:
    """Extract the best postcode from an address string.

    Tries full postcode first (e.g. "SL6 1AA"), then falls back to
    outcode only (e.g. "SL6"). Returns empty string if nothing found.
    """
    m = _FULL_POSTCODE_RE.search(address)
    if m:
        return m.group(0).strip().upper()
    m = _OUTCODE_RE.search(address)
    if m:
        return m.group(0).strip().upper()
    return ""


def _is_outcode(s: str) -> bool:
    """True if the string is a partial postcode (outcode) like 'SL6' or 'SW1E'."""
    return bool(re.match(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$", s))


# Maps enrichment field names to the set of column headers they populate.
# Used by /backfill-view to determine which fields to run for empty columns.
_ENRICHMENT_FIELD_COLUMNS: dict[str, set[str]] = {
    "simon": {"Simon London (min)", "Simon London Cost (£)", "Simon London Route", "Simon Parking Cost (£)"},
    "lorena": {"Lorena London (min)", "Lorena London Cost (£)", "Lorena London Route"},
    "petrol": {"Bracknell Time (min)", "Bracknell Cost (£)"},
    "schools": {
        "Primary School",
        "Primary Distance (km)",
        "Primary Walk (min)",
        "Primary School Link",
        "Primary Ofsted",
        "Primary Inspection Year",
        "Secondary School",
        "Secondary Distance (km)",
        "Secondary Walk (min)",
        "Secondary School Link",
        "Secondary Ofsted",
        "Secondary Inspection Year",
        "Secondary Bus (min)",
        "Secondary Bus Route",
    },
    "walk_time": {"Walk to Town (min)"},
    "amenities": {"Walkable Amenities"},
    "town": {"Area Description"},
    "epc": {"EPC Rating"},
    "council_tax": {"Council Tax Band", "Council Tax Cost (£)"},
    "geo": {
        "Approx Latitude (est)",
        "Approx Longitude (est)",
        "Approx Station CRS",
        "Approx Station Name",
    },
}

_HEADER_TO_ENRICHMENT_FIELD: dict[str, str] = {}
for _field, _headers in _ENRICHMENT_FIELD_COLUMNS.items():
    for _h in _headers:
        _HEADER_TO_ENRICHMENT_FIELD[_h] = _field


@asynccontextmanager
async def lifespan(_app: FastAPI):
    level = logging.DEBUG if settings.trace else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if settings.trace:
        logging.getLogger("houses.enricher").setLevel(logging.DEBUG)
        logging.getLogger("houses.server").setLevel(logging.DEBUG)
    # httpx logs full URLs including query params — suppress to avoid
    # leaking API keys in the server log
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logger.info("Houses server starting" + (" (TRACE enabled)" if settings.trace else ""))
    yield
    logger.info("Houses server shutting down")
    await stop_chrome()


app = FastAPI(
    title="Houses — Property Enrichment Engine",
    version="0.1.0",
    lifespan=lifespan,
)


def _get_properties_data() -> list[dict[str, str]]:
    """Read all properties from the Data tab and return them as dicts."""
    from houses.sheets import get_client

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
        logger.warning("Failed to read properties data: %s", e)
        return []


@app.get("/properties")
async def list_properties():
    """List all properties with their enrichment data."""
    props = _get_properties_data()
    return {"properties": props}


@app.get("/properties/{rid}")
async def get_property(rid: str):
    """Get a single property by Rightmove ID."""
    for p in _get_properties_data():
        if p.get("Rightmove ID", "").strip() == rid:
            return p
    return JSONResponse({"error": "property not found"}, status_code=404)


@app.post("/properties", response_model=None)
async def upsert_property(
    payload: Property | None = None,
    no_write: bool = Query(default=False),
    fields: list[str] | None = Query(default=None),
    rids: str | None = Query(default=None),
) -> JSONResponse | StreamingResponse:
    """Upsert a property — enrich it and write to the sheet.

    Two modes:
    1. **Single property** — provide a JSON body with url/address/postcode.
    2. **Batch re-enrich** — use query params ``rids``, ``fields``, ``no_write``.

    Always runs enrichment. Use ``no_write=true`` to cache results without
    writing to the sheet.
    """
    if payload:
        # ── Single property mode ───────────────────────────────────
        postcode = payload.postcode or extract_postcode(payload.address)
        lookup = payload.address if _is_outcode(postcode) else postcode
        address = payload.address

        # Check for existing
        rid_match = re.search(r"properties/(\d+)", payload.url)
        rid = rid_match.group(1) if rid_match else ""
        if not fields and rid:
            gclient = get_client()
            if gclient and settings.sheet_id:
                try:
                    sh = gclient.open_by_key(settings.sheet_id)
                    ws = sh.worksheet("Properties Data")
                    if any(
                        row[col_index("Rightmove ID")].strip() == rid
                        for row in ws.get_all_values()[1:]
                    ):
                        return JSONResponse(
                            content={
                                "status": "error",
                                "error": f"Property {rid} already exists. Use fields= to re-enrich specific fields.",
                            },
                            status_code=400,
                        )
                except Exception:
                    pass

        scrape_error = None
        if not address and payload.url:
            try:
                scraped = await scrape_rightmove(payload.url)
                if scraped.get("address"):
                    address = scraped["address"]
                if scraped.get("postcode") and not payload.postcode:
                    payload.postcode = scraped["postcode"]
                if scraped.get("bedrooms") is not None and payload.bedrooms is None:
                    payload.bedrooms = scraped["bedrooms"]
                if scraped.get("price") is not None and payload.price is None:
                    payload.price = scraped["price"]
                postcode = payload.postcode or extract_postcode(address)
                lookup = address if _is_outcode(postcode) else postcode
            except Exception as e:
                scrape_error = str(e)
                logger.warning("Scrape failed for %s: %s", payload.url, e)

        enabled = set(fields) if fields else None
        enriched = await _run_enrichment(
            url=payload.url, address=address, postcode=postcode, lookup=lookup,
            bedrooms=payload.bedrooms, price=payload.price, enabled=enabled,
            actual_latitude=payload.actual_latitude, actual_longitude=payload.actual_longitude,
        )

        row_url = None
        if not no_write:
            row_url = await write_enriched_row(enriched, payload.tab)

        dump = _asdict_serializable(enriched)
        extra: dict[str, Any] = {}
        if scrape_error:
            extra["scrape_warning"] = scrape_error
            dump["_scrape_warning"] = scrape_error

        if row_url:
            return JSONResponse(content={"status": "ok", "row_url": row_url, "data": dump, **extra}, status_code=201)
        return JSONResponse(
            content={"status": "ok", "note": "Sheets not configured", "data": dump, **extra}, status_code=200
        )

    # ── Batch mode ────────────────────────────────────────────────
    if not settings.sheet_id:
        async def _empty():
            yield json.dumps({"status": "ok", "note": "Sheets not configured", "results": []}) + "\n"
        return StreamingResponse(_empty(), media_type="text/plain")

    gclient = get_client()
    if gclient is None:
        async def _empty():
            yield json.dumps({"status": "ok", "note": "Sheets not configured", "results": []}) + "\n"
        return StreamingResponse(_empty(), media_type="text/plain")

    return StreamingResponse(_batch_stream(gclient, no_write, fields, rids), media_type="text/plain")


async def _batch_stream(
    gclient: Any,
    no_write: bool,
    fields: list[str] | None,
    rids: str | None,
) -> AsyncGenerator[str, None]:
    """Backfill enrichment: read View tab, enrich missing fields, yield NDJSON."""
    try:
        sh = gclient.open_by_key(settings.sheet_id)
        view_ws = sh.worksheet("Properties View")
        data_ws = sh.worksheet("Properties Data")
    except Exception as exc:
        logger.error("Failed to open sheet: %s", exc)
        yield json.dumps({"status": "error", "error": str(exc)}) + "\n"
        return

    view_data = view_ws.get_all_values()
    if len(view_data) < 2:
        yield json.dumps({"status": "ok", "message": "View tab is empty", "results": []}) + "\n"
        return

    view_headers = view_data[0]
    vh = {h.strip().lower(): i for i, h in enumerate(view_headers)}
    url_col = vh.get("rightmove link")
    id_col = vh.get("rightmove id")
    addr_col = vh.get("listing address")

    data_all = data_ws.get_all_values()
    data_headers = data_all[0] if data_all else []

    try:
        data_rid_idx = data_headers.index("Rightmove ID")
    except ValueError:
        data_rid_idx = -1

    user_cols = frozenset({"Actual Latitude", "Actual Longitude"})
    enriched_col_indices: dict[str, int] = {}
    for i, h in enumerate(data_headers):
        if h not in user_cols:
            enriched_col_indices[h] = i

    user_fields: set[str] | None = None
    if fields:
        user_fields = set()
        for f in fields:
            for part in f.split(","):
                p = part.strip()
                if p:
                    user_fields.add(p)
    target_rids: set[str] = {r.strip() for r in rids.split(",")} if rids else set()
    processed_rids: set[str] = set()
    total = len(view_data) - 1

    yield json.dumps({"type": "start", "total": total, "no_write": no_write, "force": True, "rids": rids}) + "\n"

    from houses.sheets import row_values as _row_values

    for row_idx, view_row in enumerate(view_data[1:], 2):
        url_raw = view_row[url_col].strip() if url_col is not None and url_col < len(view_row) else ""
        raw_id = view_row[id_col].strip() if id_col is not None and id_col < len(view_row) else ""
        rid = _rightmove_id(raw_id) if raw_id else _rightmove_id(url_raw)
        if not rid:
            yield _json_line({"type": "row", "row": row_idx, "rid": None, "status": "skipped", "reason": "no RID"})
            continue
        if target_rids and rid not in target_rids:
            yield _json_line({"type": "row", "row": row_idx, "rid": rid, "status": "skipped", "reason": "not in rids"})
            continue
        if rid in processed_rids:
            yield json.dumps({"type": "row", "row": row_idx, "rid": rid, "status": "skipped", "reason": "duplicate"}) + "\n"
            continue
        processed_rids.add(rid)

        url = url_raw if url_raw.startswith("http") else f"https://www.rightmove.co.uk/properties/{rid}"
        address = view_row[addr_col].strip() if addr_col is not None and addr_col < len(view_row) else ""
        data_row = data_all[row_idx - 1] if len(data_all) > row_idx - 1 else [""] * len(data_headers)
        existing_rid = data_row[data_rid_idx].strip() if data_rid_idx >= 0 and len(data_row) > data_rid_idx else ""
        data_row_num = row_idx if (existing_rid == rid or not existing_rid) else 0

        if data_row_num == 0:
            # New property
            yield json.dumps({"type": "row", "row": row_idx, "rid": rid, "status": "enriching"}) + "\n"
            new_pc = extract_postcode(address) or ""
            enriched = await _run_backfill_enrichment(url=url, address=address, postcode=new_pc, lookup=new_pc, bedrooms=None, price=None, enabled=None)
            flat = _row_values(enriched)
            if no_write:
                yield json.dumps({"type": "row", "row": row_idx, "rid": rid, "status": "would_create", "enriched": _asdict_serializable(enriched), "flat": flat}) + "\n"
            else:
                row_url_result = await write_enriched_row(enriched)
                yield json.dumps({"type": "row", "row": row_idx, "rid": rid, "status": "created", "row_url": row_url_result}) + "\n"
            continue

        # Existing property
        empty_headers = [h for h, ci in enriched_col_indices.items() if ci >= len(data_row) or not data_row[ci].strip()]

        if user_fields:
            forced = []
            for h in data_headers:
                ef = _HEADER_TO_ENRICHMENT_FIELD.get(h)
                if ef and ef in user_fields:
                    forced.append(h)
            empty_headers = list(set(empty_headers) | set(forced))

        if not empty_headers:
            yield json.dumps({"type": "row", "row": row_idx, "rid": rid, "status": "skipped", "reason": "already fully enriched"}) + "\n"
            continue

        needed = set()
        for h in empty_headers:
            ef = _HEADER_TO_ENRICHMENT_FIELD.get(h)
            if ef:
                needed.add(ef)
        if user_fields is not None:
            needed &= user_fields

        addr = data_row[1].strip() if len(data_row) > 1 and data_row[1].strip() else address
        pc = data_row[2].strip() if len(data_row) > 2 and data_row[2].strip() else extract_postcode(addr)
        if "epc" in needed and (not pc or _is_outcode(pc)):
            needed.discard("epc")
        if "council_tax" in needed and not addr:
            needed.discard("council_tax")

        if not needed:
            yield json.dumps({"type": "row", "row": row_idx, "rid": rid, "status": "skipped", "reason": "no fields"}) + "\n"
            continue

        yield json.dumps({"type": "row", "row": row_idx, "rid": rid, "status": "enriching", "fields": sorted(needed)}) + "\n"
        enriched = await _run_backfill_enrichment(url=url, address=addr, postcode=pc, lookup=addr if _is_outcode(pc) else pc, bedrooms=None, price=None, enabled=needed if needed else None)

        if no_write:
            flat = _row_values(enriched)
            status = "would_create" if not existing_rid else "would_update"
            yield json.dumps({"type": "row", "row": row_idx, "rid": rid, "status": status, "fields": sorted(needed), "enriched": _asdict_serializable(enriched), "flat": flat}) + "\n"
        else:
            _write_backfill_cells(sh, data_ws, data_row_num, data_headers, data_row, enriched, empty_headers)
            yield json.dumps({"type": "row", "row": row_idx, "rid": rid, "status": "updated", "fields": sorted(needed)}) + "\n"


@app.post("/properties/compare", response_model=None)
async def compare_properties(
    rids: str | None = Query(default=None),
    fields: list[str] | None = Query(default=None),
) -> StreamingResponse:
    """Compare current sheet data with a fresh no-write re-enrichment.

    Returns a TSV diff with columns RID, Field, Old (sheet), New (enriched).
    This is POST because it triggers enrichment (API calls, caching).
    """

    # Read sheet data first
    props = _get_properties_data()

    # Build enriched flat dicts by calling _run_backfill_enrichment per property
    import csv
    import io

    # Run enrichment in no-write mode for each property
    enriched_rows: dict[str, dict[str, str]] = {}
    from houses.sheets import row_values

    for view_row_idx, data_row in enumerate(
        [list(p.values()) for p in props], 2
    ):
        rid = data_row[col_index("Rightmove ID")] if col_index("Rightmove ID") < len(data_row) else ""
        if not rid:
            continue
        if rids and rid not in {r.strip() for r in rids.split(",")}:
            continue

        address = data_row[col_index("Address")] if col_index("Address") < len(data_row) else ""
        postcode = data_row[col_index("Postcode")] if col_index("Postcode") < len(data_row) else ""
        url = data_row[col_index("Rightmove URL")] if col_index("Rightmove URL") < len(data_row) else (
            f"https://www.rightmove.co.uk/properties/{rid}"
        )

        enriched = await _run_backfill_enrichment(
            url=url, address=address, postcode=postcode,
            lookup=address, bedrooms=None, price=None,
            enabled=set(fields) if fields else None,
        )
        enriched_rows[rid] = row_values(enriched)

    # Build TSV diff
    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow(["RID", "Field", "Old (sheet)", "New (enriched)"])

    diff_count = 0
    for p in props:
        rid = p.get("Rightmove ID", "").strip()
        if not rid or rid not in enriched_rows:
            continue
        new_data = enriched_rows[rid]
        for header, old_val in p.items():
            stripped = header.strip()
            if not stripped or stripped in ("Rightmove ID",):
                continue
            new_val = new_data.get(stripped, "")
            old_clean = old_val.strip() if old_val else ""
            if old_clean != new_val:
                diff_count += 1
                writer.writerow([rid, stripped, old_clean, new_val])

    writer.writerow([])
    writer.writerow(["DIFF_COUNT", str(diff_count), "", ""])
    result = output.getvalue()

    return StreamingResponse(iter([result]), media_type="text/tab-separated-values")


@app.post("/sync-view-formulas")
async def sync_view_formulas_endpoint() -> JSONResponse:
    """Refresh View tab formulas and named ranges to match the current Data tab."""
    if not settings.sheet_id:
        return JSONResponse(content={"status": "ok", "note": "Sheets not configured"})
    gclient = get_client()
    if gclient is None:
        return JSONResponse(content={"status": "ok", "note": "Sheets not configured"})
    try:
        sh = gclient.open_by_key(settings.sheet_id)
        sync_view_formulas(sh)
        logger.info("View formulas synced")
        return JSONResponse(content={"status": "ok", "message": "View formulas synced"})
    except Exception as exc:
        logger.error("Failed to sync view formulas: %s", exc)
        return JSONResponse(content={"status": "error", "error": str(exc)}, status_code=500)


def _json_line(data: dict) -> str:
    """Pretty-print JSON for streaming output lines."""
    return json.dumps(data) + "\n"


async def _run_enrichment(
    url: str,
    address: str,
    postcode: str,
    lookup: str,
    bedrooms: int | None = None,
    price: float | None = None,
    enabled: set[str] | None = None,
    actual_latitude: float | None = None,
    actual_longitude: float | None = None,
) -> EnrichedProperty:
    """Run enrichment for the given set of fields and return an EnrichedProperty.

    ``enabled`` is a set of field names (e.g. ``{"simon", "lorena", "petrol"}``)
    or None to run all fields.

    If ``address`` is empty, attempts to scrape the property details from
    Rightmove via Chrome CDP before running enrichment.

    ``actual_latitude`` / ``actual_longitude`` are user-provided overrides that
    take precedence over scraped or geocoded values for approx_lat/lng.

    Geo enrichment always tries ``scrape_rightmove(url)`` first (cache-first),
    falls back to geocoding, and respects ``actual_lat/lng`` override.
    """
    # ── Scrape Rightmove if address is missing ──
    if not address:
        scraped = await scrape_rightmove(url)
        if scraped.get("address"):
            address = scraped["address"]
        if scraped.get("postcode") and not postcode:
            postcode = scraped["postcode"]
        if scraped.get("bedrooms") is not None and bedrooms is None:
            bedrooms = scraped["bedrooms"]
        if scraped.get("price") is not None and price is None:
            price = scraped["price"]

    if not lookup:
        lookup = address if _is_outcode(postcode) else postcode

    simon = Commute(destination_label="Simon (London)", destination_postcode=postcode)
    lorena = Commute(destination_label="Lorena (London)", destination_postcode=postcode)
    petrol = Commute(destination_label="Bracknell Office (RG12 8YA)", destination_postcode=settings.bracknell_postcode)
    primary = None
    secondary = None
    town_desc = ""
    walk_data: dict[str, Any] = {"walk_to_town_minutes": None, "amenities": ""}
    epc = ""
    breakdown = CommuteBreakdown()
    approx_lat = None
    approx_lng = None
    station_crs = ""
    station_name = ""
    council_tax = None

    # Single PropertyLocation — resolve once for all enrichment steps
    location = PropertyLocation(postcode=postcode, address=lookup or address)
    location = await location.resolve()
    approx_lat = location.coordinates.value_or_none().lat if location.coordinates.is_succeeded else None
    approx_lng = location.coordinates.value_or_none().lon if location.coordinates.is_succeeded else None

    if enabled is None or "simon" in enabled:
        simon = (await compute_simon_commute(lookup)).value_or_none()
    if enabled is None or "lorena" in enabled:
        lorena = (await compute_lorena_commute(lookup)).value_or_none()
    if enabled is None or "petrol" in enabled:
        petrol = (await compute_petrol_cost(postcode)).value_or_none()

    # School enrichment defaults (may be overridden below)
    primary = None
    primary_commute = None
    primary_dist = None
    secondary = None
    secondary_commute = None
    secondary_dist = None

    if enabled is None or "schools" in enabled:
        loc_coords = location.coordinates.value_or_none()
        primary = await find_nearest(postcode, child_age=7, address=address, requirement=SchoolGender.BOYS)
        primary_commute = await compute_school_commute(postcode, primary) if primary else None
        primary_dist = (
            round(loc_coords.distance_km_to(primary.coords), 2)
            if primary and primary.coords and loc_coords else None
        )
        secondary = await find_nearest(postcode, child_age=12, address=address, requirement=SchoolGender.BOYS)
        secondary_commute = await compute_school_commute(postcode, secondary) if secondary else None
        secondary_dist = (
            round(loc_coords.distance_km_to(secondary.coords), 2)
            if secondary and secondary.coords and loc_coords else None
        )
    if enabled is None or {"walk_time", "amenities"} & enabled:
        coords = location.coordinates.value_or_none()
        walk_data = (
            await enrich_walkability(coords.lat, coords.lon, address)
            if coords
            else {"walk_to_town_minutes": None, "amenities": ""}
        )
    if enabled is None or "town" in enabled:
        town_name = ""
        if address:
            parts = [p.strip() for p in address.split(",")]
            outcode_re = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$")
            postcode_re = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$", re.IGNORECASE)
            candidates = [p for p in parts if p and not postcode_re.match(p) and not outcode_re.match(p)]
            non_county = [p for p in candidates if p.lower().strip() not in KNOWN_COUNTIES]
            town_name = non_county[-1] if non_county else (candidates[-1] if candidates else "")
        town_desc = await generate_town_description(town_name, postcode)

    simon, lorena = await _enrich_rail_fares(enabled, postcode, address, simon, lorena)

    if simon and lorena and petrol and (enabled is None or {"simon", "lorena", "petrol"} & enabled):
        breakdown = await compute_commute_breakdown(simon, lorena, petrol)

    if enabled is None or "epc" in enabled:
        epc = await lookup_epc(postcode, address) if postcode and not _is_outcode(postcode) else ""

    if (enabled is None or "council_tax" in enabled) and postcode and not _is_outcode(postcode) and address:
        result = await lookup_council_tax(postcode, address)
        council_tax = result.value_or_none()
        if result.is_impossible:
            logger.debug("Council tax: %s for %s", result.reason, postcode)

    if enabled is None or "geo" in enabled:
        if actual_latitude is not None and actual_longitude is not None:
            approx_lat, approx_lng = actual_latitude, actual_longitude
        else:
            scraped_geo = await scrape_rightmove(url)
            if scraped_geo.get("latitude") is not None and scraped_geo.get("longitude") is not None:
                approx_lat, approx_lng = scraped_geo["latitude"], scraped_geo["longitude"]
            # else: approx_lat/lng already set from shared PropertyLocation above

        if approx_lat is not None and approx_lng is not None:
            station = nearest_station(approx_lat, approx_lng)
            if station:
                station_crs = station["crs"]
                station_name = station["name"]

    return EnrichedProperty(
        url=url,
        address=address,
        postcode=postcode,
        bedrooms=bedrooms or 0,
        price=price or 0.0,
        simon_commute=simon,
        lorena_commute=lorena,
        petrol=petrol,
        commute_breakdown=breakdown,
        primary_school=primary,
        primary_school_commute=primary_commute,
        primary_school_distance_km=primary_dist,
        secondary_school=secondary,
        secondary_school_commute=secondary_commute,
        secondary_school_distance_km=secondary_dist,
        town_description=town_desc,
        walk_to_town_minutes=walk_data.get("walk_to_town_minutes"),
        walkable_amenities=walk_data.get("amenities", ""),
        primary_ofsted=primary.ofsted_rating if primary else "",
        secondary_ofsted=secondary.ofsted_rating if secondary else "",
        primary_inspection_year=primary.inspection_year if primary else "",
        secondary_inspection_year=secondary.inspection_year if secondary else "",
        epc_rating=epc,
        council_tax=council_tax,
        approx_latitude=approx_lat,
        approx_longitude=approx_lng,
        approx_station_crs=station_crs,
        approx_station_name=station_name,
    )


async def _run_backfill_enrichment(
    url: str,
    address: str,
    postcode: str,
    lookup: str,
    bedrooms: int | None,
    price: float | None,
    enabled: set[str] | None,
) -> EnrichedProperty:
    return await _run_enrichment(
        url=url,
        address=address,
        postcode=postcode,
        lookup=lookup,
        bedrooms=bedrooms,
        price=price,
        enabled=enabled,
    )


async def _enrich_rail_fares(
    enabled: set[str] | None,
    postcode: str,
    address: str,
    simon: Commute,
    lorena: Commute,
) -> tuple[Commute, Commute]:
    """Fallback: look up National Rail fares when TfL didn't return a cost."""
    needs_rail = enabled is None or enabled & {"simon"} or enabled & {"lorena"}
    if not needs_rail:
        return simon, lorena

    # Determine which commutes need NR fare lookup:
    # - No TfL cost at all (daily_cost_gbp is None)
    # - OR the cost is only the bus/parking component (no rail fare added yet)
    def _has_rail_fare(commute: Commute) -> bool:
        """True when ``daily_cost_gbp`` is explicitly set and includes more than
        just the bus or parking component — meaning rail is already priced."""
        if commute.daily_cost_gbp is None:
            return False
        non_rail = commute.non_rail_cost()
        if non_rail > 0:
            # If daily_cost_gbp == non-rail cost alone, rail is missing
            return commute.daily_cost_gbp != non_rail
        return True

    simon_needs = simon is not None and simon.duration_minutes is not None and not _has_rail_fare(simon)
    lorena_needs = lorena is not None and lorena.duration_minutes is not None and not _has_rail_fare(lorena)

    if not simon_needs and not lorena_needs:
        return simon, lorena

    tube_single = 2.80
    fare_pc = postcode or extract_postcode(address)
    if not fare_pc:
        return simon, lorena
    fare_coords = (await geocode(fare_pc)).value_or_none()
    if not fare_coords:
        return simon, lorena
    station = nearest_station(fare_coords.lat, fare_coords.lon)
    if not station:
        return simon, lorena
    if simon_needs:
        f = fare_between(station["crs"], settings.simon_station_crs)
        if f is not None:
            rail_cost = round((f + tube_single) * 2, 2)
            parking = simon.non_rail_cost()
            simon = Commute(
                destination_label=simon.destination_label,
                destination_postcode=simon.destination_postcode,
                duration_minutes=simon.duration_minutes,
                daily_cost_gbp=rail_cost + parking,
                mode=simon.mode,
                cost_groups=simon.cost_groups,
            )
            logger.info(
                "NR fare fallback for Simon: £%.2f (rail) + £%.2f (parking) = £%.2f",
                rail_cost,
                parking,
                rail_cost + parking,
            )
    if lorena_needs:
        f = fare_between(station["crs"], settings.lorena_station_crs)
        if f is not None:
            rail_cost = round((f + tube_single) * 2, 2)
            bus = lorena.non_rail_cost()
            lorena = Commute(
                destination_label=lorena.destination_label,
                destination_postcode=lorena.destination_postcode,
                duration_minutes=lorena.duration_minutes,
                daily_cost_gbp=rail_cost + bus,
                mode=lorena.mode,
                cost_groups=lorena.cost_groups,
            )
            logger.info(
                "NR fare fallback for Lorena: £%.2f (rail) + £%.2f (bus) = £%.2f",
                rail_cost,
                bus,
                rail_cost + bus,
            )

    return simon, lorena


def _write_backfill_cells(
    sh: Any,
    ws: Any,
    row_num: int,
    headers: list[str],
    current_row: list[str],
    enriched: EnrichedProperty,
    allowed_headers: list[str],
    force: bool = False,
) -> None:
    """Write enriched values to a Data tab row.

    Normally only writes to cells that are currently empty (never overwrites
    existing data). When ``force=True``, writes to all allowed_headers even
    if the cell already has data.
    """
    enriched_dict = row_values(enriched)
    allowed_set = set(allowed_headers)

    cells: list[dict[str, Any]] = []
    for name, val in enriched_dict.items():
        if not val or name not in allowed_set:
            continue
        try:
            col_idx = headers.index(name)
        except ValueError:
            continue
        if not force and col_idx < len(current_row) and current_row[col_idx].strip():
            continue  # cell already has data — never overwrite unless force
        cl = col_letter(col_idx)
        cells.append({"range": f"{cl}{row_num}", "values": [[val]]})

    if cells:
        Tab(ws).batch_update(cells)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})
