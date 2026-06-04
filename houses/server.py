"""FastAPI app — /inject-property endpoint, startup/shutdown."""

import logging
import re
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query
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
from houses.models import CommuteBreakdown, EnrichedProperty, PetrolCost, PropertyPayload, TransitInfo
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


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})
