"""FastAPI app — /inject-property endpoint, startup/shutdown."""

import logging
import re
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query  # noqa: F401
from fastapi.responses import JSONResponse

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
from houses.models import CommuteBreakdown, EnrichedProperty, PetrolCost, PropertyPayload, ReprocessRequest, TransitInfo
from houses.rail_fares import fare_between, nearest_station
from houses.sheets import col_index, write_enriched_row
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
    col_index("Rightmove ID")
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

    # NR rail fare fallback for missing TfL fares (only if simon or lorena computed)
    needs_rail = enabled is None or enabled & {"simon"} or enabled & {"lorena"}
    if needs_rail and (simon.daily_cost_gbp is None or lorena.daily_cost_gbp is None):
        tube_single = 2.80
        fare_coords = await _geocode(postcode)
        if fare_coords:
            station = nearest_station(fare_coords[0], fare_coords[1])
            if station:
                if simon.daily_cost_gbp is None:
                    f = fare_between(station["crs"], settings.simon_station_crs)
                    if f is not None:
                        simon.daily_cost_gbp = round((f + tube_single) * 2, 2)
                if lorena.daily_cost_gbp is None:
                    f = fare_between(station["crs"], settings.lorena_station_crs)
                    if f is not None:
                        lorena.daily_cost_gbp = round((f + tube_single) * 2, 2)

    if enabled is None or (enabled & {"simon"} and enabled & {"lorena"} and enabled & {"petrol"}):
        breakdown = await compute_commute_breakdown(simon, lorena, petrol)

    # EPC
    if enabled is None or enabled & {"epc"}:
        epc = await lookup_epc(postcode) if not _is_outcode(postcode) else ""

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
        secondary_inspection_year=secondary.inspection_year if secondary else "",
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

    # dry_run skips sheet write — returns data with 200
    if dry_run:
        return JSONResponse(content={"status": "ok", "data": dump}, status_code=200)

    if row_url:
        logger.info("Written to sheet: %s", row_url)
        return JSONResponse(
            content={"status": "ok", "row_url": row_url, "data": dump},
            status_code=201,
        )

    return JSONResponse(
        content={"status": "ok", "note": "Sheets not configured", "data": dump},
        status_code=200,
    )


@app.post("/reprocess")
async def reprocess(
    fields: list[str] | None = Query(default=None),  # noqa: B008
    body: ReprocessRequest | None = None,
) -> JSONResponse:
    """Re-run enrichment for existing properties.

    Reads all rows from the sheet, re-runs the specified enrichment fields,
    and writes only those columns back in-place.

    - Omit ``ids`` to reprocess every row that has the necessary data.
    - Provide ``ids`` to reprocess only specific Rightmove IDs.
    """
    if not settings.sheet_id:
        return JSONResponse(content={"error": "Sheet not configured"}, status_code=400)

    from houses.sheets import get_client

    gclient = get_client()
    if not gclient:
        return JSONResponse(content={"error": "Sheets credentials not configured"}, status_code=400)

    try:
        sh = gclient.open_by_key(settings.sheet_id)
        ws = sh.worksheet("Properties Data")
        all_rows = ws.get_all_values()
    except Exception as e:
        logger.error("Failed to read sheet: %s", e)
        return JSONResponse(content={"error": f"Failed to read sheet: {e}"}, status_code=500)

    if not all_rows:
        return JSONResponse(content={"error": "Sheet is empty"}, status_code=400)

    headers = all_rows[0]

    def col(h: str) -> int:
        try:
            return headers.index(h)
        except ValueError:
            return -1

    col_url = col("Rightmove URL")
    col_addr = col("Address")
    col_pc = col("Postcode")
    col_rid = col("Rightmove ID")

    if col_rid < 0:
        return JSONResponse(content={"error": "Sheet missing Rightmove ID column"}, status_code=400)

    allowed_ids = set(body.ids) if body and body.ids else None
    target_ids: set[str] = set()
    rows_by_id: dict[str, dict[str, str]] = {}

    for row in all_rows[1:]:
        if len(row) <= col_rid:
            continue
        rid = row[col_rid].strip()
        if not rid:
            continue
        if allowed_ids is not None and rid not in allowed_ids:
            continue
        target_ids.add(rid)
        rows_by_id[rid] = {
            "url": row[col_url].strip() if col_url >= 0 and len(row) > col_url else "",
            "address": row[col_addr].strip() if col_addr >= 0 and len(row) > col_addr else "",
            "postcode": row[col_pc].strip() if col_pc >= 0 and len(row) > col_pc else "",
        }

    if not target_ids:
        if allowed_ids:
            return JSONResponse(content={"error": "None of the requested IDs were found in the sheet"}, status_code=404)
        return JSONResponse(content={"error": "No rows with Rightmove IDs found in sheet"}, status_code=400)

    enabled = set(fields)
    results: dict[str, str] = {}
    processed = 0

    for rid in sorted(target_ids):
        row_data = rows_by_id[rid]
        url = row_data["url"]
        address = row_data["address"]
        postcode = row_data["postcode"]

        # The stored URL might be a description text, not a real URL.
        # write_enriched_row uses _rightmove_id which parses the path.
        # Ensure we have a valid URL so existing rows can be found.
        if not url or "properties/" not in url:
            url = f"https://www.rightmove.co.uk/properties/{rid}"
        if not url:
            results[rid] = "skipped: no URL"
            continue

        lookup_pc = postcode or extract_postcode(address)

        enriched = EnrichedProperty(
            url=url,
            address=address,
            postcode=lookup_pc,
        )

        if "council_tax" in enabled and lookup_pc and address:
            ct = await lookup_council_tax(lookup_pc, address)
            enriched.council_tax = ct

        if "simon" in enabled and lookup_pc:
            from houses.enricher import compute_simon_commute

            enriched.simon_commute = await compute_simon_commute(lookup_pc)

        if "lorena" in enabled and lookup_pc:
            from houses.enricher import compute_lorena_commute

            enriched.lorena_commute = await compute_lorena_commute(lookup_pc)

        if "petrol" in enabled and lookup_pc:
            from houses.enricher import compute_petrol_cost

            enriched.petrol = await compute_petrol_cost(lookup_pc)

        if "schools" in enabled and lookup_pc:
            from houses.enricher import find_nearest_boys_primary, find_nearest_boys_secondary

            enriched.primary_school = await find_nearest_boys_primary(lookup_pc, address)
            enriched.secondary_school = await find_nearest_boys_secondary(lookup_pc, address)

        if "town" in enabled and (address or lookup_pc):
            from houses.town_desc import generate_town_description

            town_name = ""
            if address:
                parts = [p.strip() for p in address.split(",")]
                pc_re = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$", re.IGNORECASE)
                oc_re = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$")
                candidates = [p for p in parts if p and not pc_re.match(p) and not oc_re.match(p)]
                town_name = candidates[-1] if candidates else ""
            enriched.town_description = await generate_town_description(town_name, lookup_pc)

        if "epc" in enabled and lookup_pc and not _is_outcode(lookup_pc):
            from houses.epc import lookup_epc

            enriched.epc_rating = await lookup_epc(lookup_pc)

        try:
            row_url = await write_enriched_row(enriched, "Properties Data")
            results[rid] = "updated" if row_url else "write_skipped"
            processed += 1
        except Exception as e:
            logger.error("Failed to write row %s: %s", rid, e)
            results[rid] = f"error: {e}"

    return JSONResponse(
        content={
            "status": "ok",
            "processed": processed,
            "total_requested": len(target_ids),
            "results": results,
        }
    )


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})
