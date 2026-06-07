"""FastAPI app — /inject-property endpoint, startup/shutdown."""

import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, StreamingResponse

from houses.config import settings
from houses.council_tax import lookup_council_tax
from houses.enricher import (
    _geocode,
    _geocode_address,
    compute_commute_breakdown,
    compute_lorena_commute,
    compute_petrol_cost,
    compute_simon_commute,
    find_nearest_boys_primary,
    find_nearest_boys_secondary,
)
from houses.epc import lookup_epc
from houses.models import CommuteBreakdown, EnrichedProperty, PetrolCost, PropertyPayload, TransitInfo
from houses.rail_fares import fare_between, nearest_station
from houses.rightmove_scraper import scrape as scrape_rightmove
from houses.rightmove_scraper import stop_chrome
from houses.sheets import (
    _rightmove_id,
    col_index,
    col_letter,
    get_client,
    write_enriched_row,
)
from houses.town_desc import generate_town_description
from houses.walkability import _KNOWN_COUNTIES, enrich_walkability

logger = logging.getLogger(__name__)

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
    "simon": {"Simon London (min)", "Simon London Cost (£)", "Simon London Route"},
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # httpx logs full URLs including query params — suppress to avoid
    # leaking API keys in the server log
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logger.info("Houses server starting")
    yield
    logger.info("Houses server shutting down")
    await stop_chrome()


app = FastAPI(
    title="Houses — Property Enrichment Engine",
    version="0.1.0",
    lifespan=lifespan,
)


@app.post("/inject-property")
async def inject_property(
    payload: PropertyPayload,
    dry_run: bool = False,
    fields: list[str] | None = Query(default=None),  # noqa: B008
) -> JSONResponse:
    postcode = payload.postcode or extract_postcode(payload.address)

    # TfL and ORS can geocode full street addresses, but choke on outcodes
    # ("SL6" returns 300 Multiple Choices). When we only have an outcode,
    # use the full address as the lookup location instead.
    lookup = payload.address if _is_outcode(postcode) else postcode

    # Check if this property already exists in the sheet. Full enrichment without
    # explicit fields is only valid for new properties.
    rid_match = re.search(r"properties/(\d+)", payload.url)
    rid = rid_match.group(1) if rid_match else ""

    if not fields and rid:
        from houses.sheets import get_client

        gclient = get_client()
        if gclient and settings.sheet_id:
            try:
                sh = gclient.open_by_key(settings.sheet_id)
                ws = sh.worksheet(payload.tab or "Properties Data")
                existing = ws.get_all_values()
                for r in existing[1:]:
                    if len(r) > col_index("Rightmove ID") and r[col_index("Rightmove ID")].strip() == rid:
                        return JSONResponse(
                            content={
                                "error": (
                                    f"Property {rid} already exists in the sheet. "
                                    "To update it you must specify which enrichment fields to re-run: "
                                    "?fields=simon,lorena,petrol,schools,walk_time,amenities,town,epc,geo"
                                )
                            },
                            status_code=400,
                        )
            except Exception:
                pass  # If we can't check, proceed anyway

    # ── Scrape Rightmove if address is missing ──
    scrape_error: str | None = None
    if not payload.address:
        scraped = await scrape_rightmove(payload.url)
        if scraped.get("_error") == "login_required":
            scrape_error = (
                "Rightmove returned a login/verification page. Open Chrome, sign in to Rightmove, then try again."
            )
        if scraped.get("address"):
            payload.address = scraped["address"]
        if scraped.get("postcode") and not payload.postcode:
            payload.postcode = scraped["postcode"]
        if scraped.get("bedrooms") is not None and payload.bedrooms is None:
            payload.bedrooms = scraped["bedrooms"]
        if scraped.get("price") is not None and payload.price is None:
            payload.price = scraped["price"]
        if scraped.get("latitude") is not None:
            payload.actual_latitude = scraped["latitude"]
        if scraped.get("longitude") is not None:
            payload.actual_longitude = scraped["longitude"]

        # Re-derive postcode and lookup now that we have the address
        postcode = payload.postcode or extract_postcode(payload.address)
        lookup = payload.address if _is_outcode(postcode) else postcode

    logger.info(
        "Processing: %s | address=%s | postcode=%s | lookup=%s | beds=%s | price=%s | fields=%s",
        payload.url,
        payload.address,
        postcode,
        lookup,
        payload.bedrooms,
        payload.price,
        fields or "all",
    )

    # Track which enrichments completed (for logging/debug)
    enabled = set(fields) if fields else None

    simon = TransitInfo(destination_label="Simon (London)", destination_postcode=postcode)
    lorena = TransitInfo(destination_label="Lorena (London)", destination_postcode=postcode)
    petrol = PetrolCost()
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

    # Transit — TfL + NR fallback
    if enabled is None or enabled & {"simon"}:
        simon = await compute_simon_commute(lookup)
    if enabled is None or enabled & {"lorena"}:
        lorena = await compute_lorena_commute(lookup)

    # Petrol — ORS driving-car
    if enabled is None or enabled & {"petrol"}:
        petrol = await compute_petrol_cost(postcode)

    # Schools — GIAS lookup + bus routes
    if enabled is None or enabled & {"schools"}:
        primary = await find_nearest_boys_primary(postcode, payload.address)
        secondary = await find_nearest_boys_secondary(postcode, payload.address)

    # Walkability — needs coords, geocode the address first
    if enabled is None or {"walk_time", "amenities"} & enabled:
        coords = await _geocode_address(lookup)
        if coords is None:
            coords = await _geocode(postcode)
        walk_data = (
            await enrich_walkability(coords[0], coords[1], payload.address)
            if coords
            else {"walk_to_town_minutes": None, "amenities": ""}
        )

    # Town description — LLM
    if enabled is None or enabled & {"town"}:
        town_name = ""
        if payload.address:
            parts = [p.strip() for p in payload.address.split(",")]
            outcode_re = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$")
            postcode_re = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$", re.IGNORECASE)
            candidates = [p for p in parts if p and not postcode_re.match(p) and not outcode_re.match(p)]
            non_county = [p for p in candidates if p.lower().strip() not in _KNOWN_COUNTIES]
            town_name = non_county[-1] if non_county else (candidates[-1] if candidates else "")
        town_desc = await generate_town_description(town_name, postcode)

    await _enrich_rail_fares(enabled, postcode, payload.address, simon, lorena)

    if enabled is None or (enabled & {"simon"} and enabled & {"lorena"} and enabled & {"petrol"}):
        breakdown = await compute_commute_breakdown(simon, lorena, petrol)

    # EPC — requires an exact house number + street address
    if enabled is None or enabled & {"epc"}:
        has_street_addr = payload.address and payload.address[0].isdigit()
        epc = await lookup_epc(postcode) if postcode and not _is_outcode(postcode) and has_street_addr else ""

    council_tax = None
    if (enabled is None or enabled & {"council_tax"}) and postcode and payload.address:
        council_tax = await lookup_council_tax(postcode, payload.address)

    # Geocode for approx cache fields
    if enabled is None or enabled & {"geo"}:
        actual_lat = payload.actual_latitude
        actual_lng = payload.actual_longitude

        if actual_lat is not None and actual_lng is not None:
            approx_lat, approx_lng = actual_lat, actual_lng
        else:
            coords = await _geocode_address(lookup)
            approx_lat, approx_lng = coords if coords else (None, None)

        station_crs = ""
        station_name = ""
        if approx_lat is not None and approx_lng is not None:
            station = nearest_station(approx_lat, approx_lng)
            if station:
                station_crs = station["crs"]
                station_name = station["name"]

    enriched = EnrichedProperty(
        url=payload.url,
        address=payload.address,
        postcode=postcode,
        bedrooms=payload.bedrooms or 0,
        price=payload.price or 0.0,
        simon_commute=simon,
        lorena_commute=lorena,
        petrol=petrol,
        commute_breakdown=breakdown,
        primary_school=primary,
        secondary_school=secondary,
        town_description=town_desc,
        walk_to_town_minutes=walk_data.get("walk_to_town_minutes"),
        walkable_amenities=walk_data.get("amenities", ""),
        primary_ofsted=primary.ofsted_rating if primary else "",
        secondary_ofsted=secondary.ofsted_rating if secondary else "",
        primary_inspection_year=primary.inspection_year if primary else "",
        primary_inspection_summary=primary.inspection_summary if primary else "",
        secondary_inspection_year=secondary.inspection_year if secondary else "",
        secondary_inspection_summary=secondary.inspection_summary if secondary else "",
        epc_rating=epc,
        council_tax=council_tax,
        approx_latitude=approx_lat,
        approx_longitude=approx_lng,
        approx_station_crs=station_crs,
        approx_station_name=station_name,
    )

    row_url = None
    if not dry_run:
        row_url = await write_enriched_row(enriched, payload.tab)

    dump = enriched.model_dump(mode="json")

    extra: dict[str, Any] = {}
    if scrape_error:
        extra["scrape_warning"] = scrape_error
        dump["_scrape_warning"] = scrape_error

    # dry_run skips sheet write — returns data with 200
    if dry_run:
        return JSONResponse(content={"status": "ok", "data": dump, **extra}, status_code=200)

    if row_url:
        logger.info("Written to sheet: %s", row_url)
        return JSONResponse(
            content={"status": "ok", "row_url": row_url, "data": dump, **extra},
            status_code=201,
        )

    return JSONResponse(
        content={"status": "ok", "note": "Sheets not configured", "data": dump, **extra},
        status_code=200,
    )


@app.post("/backfill-view")
async def backfill_view(
    dry_run: bool = Query(default=False),
    no_write: bool = Query(default=False),
    fields: list[str] | None = Query(default=None),  # noqa: B008
) -> StreamingResponse:
    """Read Properties View tab, find properties missing enrichment, and backfill.

    For properties not yet in Properties Data: runs full enrichment and appends
    a new row. For properties already in Data but with empty enrichment cells:
    runs only the needed enrichment fields and writes only to empty cells
    (never overwrites existing data).

    Results are streamed as newline-delimited JSON so progress is visible
    in real-time.

    Query params:
      dry_run  : bool — skip all enrichment and writes, return what would happen
      no_write : bool — run enrichment (caching all API results) but skip sheet writes
      fields   : list[str] — restrict enrichment to these field groups
    """
    if not settings.sheet_id:

        async def _empty():
            yield json.dumps({"status": "ok", "note": "Sheets not configured", "results": []}) + "\n"

        return StreamingResponse(_empty(), media_type="text/plain")

    gclient = get_client()
    if gclient is None:

        async def _empty():
            yield json.dumps({"status": "ok", "note": "Sheets not configured", "results": []}) + "\n"

        return StreamingResponse(_empty(), media_type="text/plain")

    async def _stream():
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

        user_fields = set(fields) if fields else None
        processed_rids: set[str] = set()
        total = len(view_data) - 1

        yield json.dumps({"type": "start", "total": total, "dry_run": dry_run, "no_write": no_write}) + "\n"

        for row_idx, view_row in enumerate(view_data[1:], 2):
            url_raw = view_row[url_col].strip() if url_col is not None and url_col < len(view_row) else ""
            raw_id = view_row[id_col].strip() if id_col is not None and id_col < len(view_row) else ""
            rid = _rightmove_id(raw_id) if raw_id else _rightmove_id(url_raw)

            if not rid:
                yield _json_line(
                    {"type": "row", "row": row_idx, "rid": None, "status": "skipped", "reason": "no Rightmove ID"}
                )
                continue

            if rid in processed_rids:
                yield (
                    json.dumps(
                        {
                            "type": "row",
                            "row": row_idx,
                            "rid": rid,
                            "status": "skipped",
                            "reason": "duplicate RID already processed",
                        }
                    )
                    + "\n"
                )
                continue
            processed_rids.add(rid)

            url = url_raw if url_raw.startswith("http") else f"https://www.rightmove.co.uk/properties/{rid}"
            address = view_row[addr_col].strip() if addr_col is not None and addr_col < len(view_row) else ""

            data_row = data_all[row_idx - 1] if len(data_all) > row_idx - 1 else []
            if not data_row or len(data_row) < len(data_headers):
                data_row = [""] * len(data_headers)
            existing_rid = data_row[data_rid_idx].strip() if data_rid_idx >= 0 and len(data_row) > data_rid_idx else ""

            data_row_num = row_idx if (existing_rid == rid or not existing_rid) else 0

            if data_row_num != 0:
                empty_headers: list[str] = [
                    h for h, ci in enriched_col_indices.items() if ci >= len(data_row) or not data_row[ci].strip()
                ]

                if not empty_headers:
                    yield (
                        json.dumps(
                            {
                                "type": "row",
                                "row": row_idx,
                                "rid": rid,
                                "status": "skipped",
                                "reason": "already fully enriched",
                            }
                        )
                        + "\n"
                    )
                    continue

                walk_col = data_headers.index("Secondary Walk (min)") if "Secondary Walk (min)" in data_headers else -1
                if walk_col >= 0 and len(data_row) > walk_col and data_row[walk_col].strip():
                    try:
                        walk_mins = float(data_row[walk_col].strip())
                        if walk_mins is not None and walk_mins <= 20:
                            empty_headers = [
                                h for h in empty_headers if h not in ("Secondary Bus (min)", "Secondary Bus Route")
                            ]
                    except ValueError:
                        pass

                needed: set[str] = set()
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
                    if no_write:
                        yield (
                            json.dumps(
                                {
                                    "type": "row",
                                    "row": row_idx,
                                    "rid": rid,
                                    "status": "skipped",
                                    "reason": "no enrichment fields needed",
                                }
                            )
                            + "\n"
                        )
                        continue
                    # Still write user columns if they're empty
                    enriched = await _run_backfill_enrichment(
                        url=url, address=address, postcode="", lookup=address,
                        bedrooms=None, price=None, enabled=set(),
                    )
                    _write_backfill_cells(
                        sh, data_ws, data_row_num, data_headers, data_row,
                        enriched, empty_headers,
                    )
                    yield (
                        json.dumps(
                            {
                                "type": "row",
                                "row": row_idx,
                                "rid": rid,
                                "status": "user_columns_written",
                            }
                        )
                        + "\n"
                    )
                    continue

                if dry_run:
                    status = "would_create" if not existing_rid else "would_update"
                    r = {"type": "row", "row": row_idx, "rid": rid, "status": status, "fields": sorted(needed)}
                    yield _json_line(r)
                    continue

                yield (
                    json.dumps(
                        {"type": "row", "row": row_idx, "rid": rid, "status": "enriching", "fields": sorted(needed)}
                    )
                    + "\n"
                )
                enriched = await _run_backfill_enrichment(
                    url=url,
                    address=address,
                    postcode="",
                    lookup=address,
                    bedrooms=None,
                    price=None,
                    enabled=needed,
                )

                if no_write:
                    yield (
                        json.dumps(
                            {"type": "row", "row": row_idx, "rid": rid, "status": "cached", "fields": sorted(needed)}
                        )
                        + "\n"
                    )
                else:
                    _write_backfill_cells(
                        sh,
                        data_ws,
                        data_row_num,
                        data_headers,
                        data_row,
                        enriched,
                        empty_headers,
                    )
                    status = "created" if not existing_rid else "updated"
                    r = {"type": "row", "row": row_idx, "rid": rid, "status": status, "fields": sorted(needed)}
                    yield _json_line(r)
            else:
                if dry_run:
                    yield json.dumps({"type": "row", "row": row_idx, "rid": rid, "status": "would_create"}) + "\n"
                    continue

                yield json.dumps({"type": "row", "row": row_idx, "rid": rid, "status": "enriching"}) + "\n"
                enriched = await _run_backfill_enrichment(
                    url=url,
                    address=address,
                    postcode="",
                    lookup=address,
                    bedrooms=None,
                    price=None,
                    enabled=None,
                )

                if no_write:
                    yield json.dumps({"type": "row", "row": row_idx, "rid": rid, "status": "cached"}) + "\n"
                else:
                    await write_enriched_row(enriched)
                    yield json.dumps({"type": "row", "row": row_idx, "rid": rid, "status": "created"}) + "\n"

        yield json.dumps({"type": "done", "dry_run": dry_run}) + "\n"

    return StreamingResponse(_stream(), media_type="text/plain")


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
        from houses.sheets import sync_view_formulas
        sync_view_formulas(sh)
        logger.info("View formulas synced")
        return JSONResponse(content={"status": "ok", "message": "View formulas synced"})
    except Exception as exc:
        logger.error("Failed to sync view formulas: %s", exc)
        return JSONResponse(content={"status": "error", "error": str(exc)}, status_code=500)


def _json_line(data: dict) -> str:
    """Pretty-print JSON for streaming output lines."""
    return json.dumps(data) + "\n"


async def _run_backfill_enrichment(
    url: str,
    address: str,
    postcode: str,
    lookup: str,
    bedrooms: int | None,
    price: float | None,
    enabled: set[str] | None,
) -> EnrichedProperty:
    """Run enrichment for the given set of fields and return an EnrichedProperty.

    ``enabled`` is a set of field names (e.g. ``{"simon", "lorena", "petrol"}``)
    or None to run all fields.

    If ``address`` is empty, attempts to scrape the property details from
    Rightmove via Chrome CDP before running enrichment.
    """
    # ── Scrape Rightmove if address is missing ──
    if not address or not postcode:
        scraped = await scrape_rightmove(url)
        if scraped.get("address") and not address:
            address = scraped["address"]
        if scraped.get("postcode") and not postcode:
            postcode = scraped["postcode"]
        if scraped.get("bedrooms") is not None and bedrooms is None:
            bedrooms = scraped["bedrooms"]
        if scraped.get("price") is not None and price is None:
            price = scraped["price"]

    if not lookup:
        lookup = address if _is_outcode(postcode) else postcode

    simon = TransitInfo(destination_label="Simon (London)", destination_postcode=postcode)
    lorena = TransitInfo(destination_label="Lorena (London)", destination_postcode=postcode)
    petrol = PetrolCost()
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

    if enabled is None or "simon" in enabled:
        simon = await compute_simon_commute(lookup)
    if enabled is None or "lorena" in enabled:
        lorena = await compute_lorena_commute(lookup)
    if enabled is None or "petrol" in enabled:
        petrol = await compute_petrol_cost(postcode)
    if enabled is None or "schools" in enabled:
        primary = await find_nearest_boys_primary(postcode, address)
        secondary = await find_nearest_boys_secondary(postcode, address)
    if enabled is None or {"walk_time", "amenities"} & enabled:
        coords = await _geocode_address(lookup)
        if coords is None:
            coords = await _geocode(postcode)
        walk_data = (
            await enrich_walkability(coords[0], coords[1], address)
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
            non_county = [p for p in candidates if p.lower().strip() not in _KNOWN_COUNTIES]
            town_name = non_county[-1] if non_county else (candidates[-1] if candidates else "")
        town_desc = await generate_town_description(town_name, postcode)
    if enabled is None or "epc" in enabled:
        has_street_addr = address and address[0].isdigit()
        epc = await lookup_epc(postcode) if postcode and not _is_outcode(postcode) and has_street_addr else ""
    if (enabled is None or "council_tax" in enabled) and address:
        council_tax = await lookup_council_tax(postcode, address)
    await _enrich_rail_fares(enabled, postcode, address, simon, lorena)

    if enabled is None or {"simon", "lorena", "petrol"} & enabled:
        breakdown = await compute_commute_breakdown(simon, lorena, petrol)

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
        secondary_school=secondary,
        town_description=town_desc,
        walk_to_town_minutes=walk_data.get("walk_to_town_minutes"),
        walkable_amenities=walk_data.get("amenities", ""),
        primary_ofsted=primary.ofsted_rating if primary else "",
        secondary_ofsted=secondary.ofsted_rating if secondary else "",
        primary_inspection_year=primary.inspection_year if primary else "",
        primary_inspection_summary=primary.inspection_summary if primary else "",
        secondary_inspection_year=secondary.inspection_year if secondary else "",
        secondary_inspection_summary=secondary.inspection_summary if secondary else "",
        epc_rating=epc,
        council_tax=council_tax,
        approx_latitude=approx_lat,
        approx_longitude=approx_lng,
        approx_station_crs=station_crs,
        approx_station_name=station_name,
    )


async def _enrich_rail_fares(
    enabled: set[str] | None,
    postcode: str,
    address: str,
    simon: TransitInfo,
    lorena: TransitInfo,
) -> None:
    """Fallback: look up National Rail fares when TfL didn't return a cost."""
    needs_rail = enabled is None or enabled & {"simon"} or enabled & {"lorena"}
    if not needs_rail or (simon.daily_cost_gbp is not None and lorena.daily_cost_gbp is not None):
        return
    tube_single = 2.80
    fare_pc = postcode or extract_postcode(address)
    if not fare_pc:
        return
    fare_coords = await _geocode(fare_pc)
    if not fare_coords:
        return
    station = nearest_station(fare_coords[0], fare_coords[1])
    if not station:
        return
    if simon.daily_cost_gbp is None:
        f = fare_between(station["crs"], settings.simon_station_crs)
        if f is not None:
            simon.daily_cost_gbp = round((f + tube_single) * 2, 2)
    if lorena.daily_cost_gbp is None:
        f = fare_between(station["crs"], settings.lorena_station_crs)
        if f is not None:
            lorena.daily_cost_gbp = round((f + tube_single) * 2, 2)


def _write_backfill_cells(
    sh: Any,
    ws: Any,
    row_num: int,
    headers: list[str],
    current_row: list[str],
    enriched: EnrichedProperty,
    allowed_headers: list[str],
) -> None:
    """Write enriched values to a Data tab row, but only for cells that are
    currently empty. Never overwrites existing data."""
    from houses.sheets import Tab, _row_values

    enriched_dict = _row_values(enriched)
    allowed_set = set(allowed_headers)

    cells: list[dict[str, Any]] = []
    for name, val in enriched_dict.items():
        if not val or name not in allowed_set:
            continue
        try:
            col_idx = headers.index(name)
        except ValueError:
            continue
        if col_idx < len(current_row) and current_row[col_idx].strip():
            continue  # cell already has data — never overwrite
        cl = col_letter(col_idx)
        cells.append({"range": f"{cl}{row_num}", "values": [[val]]})

    if cells:
        Tab(ws).batch_update(cells)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})
