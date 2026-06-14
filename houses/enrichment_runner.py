"""Enrichment orchestration — runs the full enrichment pipeline for a single property.

This module owns the enrichment workflow: taking raw inputs (URL, address, postcode),
scraping Rightmove, calling all enrichment services, and returning an ``EnrichedProperty``.
It also defines the canonical mapping between enrichment field names and the sheet
column headers they populate.

``server.py`` routes call into this module; they don't contain enrichment logic.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from enum import Enum
from typing import Any

from money import Money

from houses.commute import Commute, CommuteBreakdown, LegMode
from houses.config import settings
from houses.context import get_rail_fare_registry
from houses.enricher import compute_commute_breakdown
from houses.geo import GeoPoint
from houses.location import (
    PropertyLocation,
    extract_postcode,
    geocode,
    is_outcode,
    resolve_house_location,
)
from houses.property import EnrichedProperty
from houses.rail_fares import RailFareRegistry
from houses.rightmove_scraper import scrape as scrape_rightmove
from houses.schools import SchoolGender
from houses.services import Services
from houses.stations import Station
from houses.walkability import KNOWN_COUNTIES

logger = logging.getLogger(__name__)


# ── JSON serialization ─────────────────────────────────────────────────


def asdict_serializable(obj: Any) -> Any:
    """Recursively convert a dataclass tree to JSON-serializable dicts.

    Like ``dataclasses.asdict()`` but also converts enums and Money to
    their values.
    """
    if isinstance(obj, Money):
        return float(obj.amount)
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj):
        return {f.name: asdict_serializable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: asdict_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [asdict_serializable(v) for v in obj]
    return obj


# ── Enrichment field ↔ column mapping ──────────────────────────────────


# Maps enrichment field names to the set of column headers they populate.
# Used by /backfill-view to determine which fields to run for empty columns.
ENRICHMENT_FIELD_COLUMNS: dict[str, set[str]] = {
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
for _field, _headers in ENRICHMENT_FIELD_COLUMNS.items():
    for _h in _headers:
        _HEADER_TO_ENRICHMENT_FIELD[_h] = _field


def header_to_enrichment_field(header: str) -> str | None:
    """Return the enrichment field name for a column header, or ``None``."""
    return _HEADER_TO_ENRICHMENT_FIELD.get(header)


# ── Enrichment pipeline ────────────────────────────────────────────────


async def _enrich_rail_fares(
    enabled: set[str] | None,
    postcode: str,
    address: str,
    simon: Commute,
    lorena: Commute,
    _registry: RailFareRegistry | None = None,
    _geocode=None,
) -> tuple[Commute, Commute]:
    """Fallback: look up National Rail fares when TfL didn't return a cost.

    ``_registry`` — optional ``RailFareRegistry`` instance (created via context var if not provided).
    ``_geocode`` — optional async geocode function (default: ``houses.location.geocode``).
    """
    registry = _registry or get_rail_fare_registry()
    geo_fn = _geocode or geocode

    needs_rail = enabled is None or enabled & {"simon"} or enabled & {"lorena"}
    if not needs_rail:
        return simon, lorena

    # Determine which commutes need NR fare lookup
    def _has_rail_fare(commute: Commute) -> bool:
        if commute.daily_cost_gbp is None:
            return False
        non_rail = commute.non_rail_cost()
        if non_rail > 0:
            return abs(float(commute.daily_cost_gbp.amount) - non_rail) > 0.01
        return True

    simon_needs = simon is not None and simon.duration_minutes is not None and not _has_rail_fare(simon)
    lorena_needs = lorena is not None and lorena.duration_minutes is not None and not _has_rail_fare(lorena)

    if not simon_needs and not lorena_needs:
        return simon, lorena

    from houses.transit_route import FALLBACK_TUBE_SINGLE_GBP, get_tube_leg_fare

    fare_pc = postcode or extract_postcode(address)
    if not fare_pc:
        return simon, lorena
    fare_coords = (await geo_fn(fare_pc)).value_or_none()
    if not fare_coords:
        return simon, lorena

    # Try to get the origin station from the actual route's first rail leg
    def _origin_station(commute: Commute) -> Station | None:
        for cg in commute.cost_groups:
            for leg in cg.legs:
                if (
                    leg.mode in (LegMode.TRAIN, LegMode.TUBE, LegMode.DLR, LegMode.OVERGROUND, LegMode.TRAM)
                    and leg.start_station
                ):
                    return registry.find_station_by_crs(Station.short_name(leg.start_station))
        return None

    origin = registry.nearest_station(fare_coords)
    if simon_needs and simon is not None:
        origin = _origin_station(simon) or origin
    if lorena_needs and lorena is not None:
        origin = _origin_station(lorena) or origin
    if not origin:
        return simon, lorena

    if simon_needs:
        dest = registry.find_station_by_crs(settings.simon_station_crs)
        if dest:
            fare = registry.fare_between(origin, dest)
            if fare is not None:
                tube_fare = await get_tube_leg_fare(dest, settings.simon_postcode)
                tube_single = tube_fare or Money(FALLBACK_TUBE_SINGLE_GBP, "GBP")
                rail_cost = (fare + tube_single) * 2
                parking = Money(str(simon.non_rail_cost()), "GBP")
                total = rail_cost + parking
                simon = Commute(
                    destination_label=simon.destination_label,
                    destination_postcode=simon.destination_postcode,
                    duration_minutes=simon.duration_minutes,
                    daily_cost_gbp=total,
                )
                logger.info(
                    "NR fare fallback for Simon: %s (rail) + %s (tube) + %s (parking) = %s",
                    str(fare.amount),
                    str(tube_single.amount),
                    str(parking.amount),
                    str(total.amount),
                )

    if lorena_needs:
        dest = registry.find_station_by_crs(settings.lorena_station_crs)
        if dest:
            fare = registry.fare_between(origin, dest)
            if fare is not None:
                tube_fare = await get_tube_leg_fare(dest, settings.lorena_postcode)
                tube_single = tube_fare or Money(FALLBACK_TUBE_SINGLE_GBP, "GBP")
                rail_cost = (fare + tube_single) * 2
                bus = Money(str(lorena.non_rail_cost()), "GBP")
                total = rail_cost + bus
                lorena = Commute(
                    destination_label=lorena.destination_label,
                    destination_postcode=lorena.destination_postcode,
                    duration_minutes=lorena.duration_minutes,
                    daily_cost_gbp=total,
                    mode=lorena.mode,
                    cost_groups=lorena.cost_groups,
                )
                logger.info(
                    "NR fare fallback for Lorena: %s (rail) + %s (tube) + %s (bus) = %s",
                    str(fare.amount),
                    str(tube_single.amount),
                    str(bus.amount),
                    str(total.amount),
                )

    return simon, lorena


async def run_enrichment(
    url: str,
    address: str,
    postcode: str,
    lookup: str,
    bedrooms: int | None = None,
    price: float | None = None,
    enabled: set[str] | None = None,
    actual_latitude: float | None = None,
    actual_longitude: float | None = None,
    services: Services | None = None,
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
        if scraped:
            if scraped.address:
                address = scraped.address
            if scraped.postcode and not postcode:
                postcode = scraped.postcode
            if scraped.bedrooms is not None and bedrooms is None:
                bedrooms = scraped.bedrooms
            if scraped.price is not None and price is None:
                price = scraped.price

    if not lookup:
        # Choose the most specific location string for routing APIs.
        #
        # A full street address (e.g. "163 Grand Drive, London, SW20 9NB")
        # is better than a bare postcode centroid because Google Routes and
        # TfL can resolve it to the exact property, not just the centre of
        # the postcode area.  This matters for first/last-leg walk distances.
        #
        # However, an address WITHOUT a postcode (e.g. "Some Road, Maidenhead")
        # can be ambiguous — there could be a "Some Road" in many towns.  In
        # that case the full postcode is more precise.
        #
        # Priority:
        #   1. Address + full postcode (address ends with outcode → upgrade)
        #   2. Full postcode (more precise than bare address without one)
        #   3. Address as-is (fallback when only an outcode or no postcode)
        #   4. Outcode or empty (last resort)
        if address and postcode and not is_outcode(postcode):
            upgraded = PropertyLocation._upgrade_address(address, postcode)
            lookup = upgraded if upgraded != address else postcode
        elif address:
            lookup = address
        elif postcode:
            lookup = postcode
        else:
            lookup = ""

    svc = services or Services()

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
        simon = (await svc.commute_router.simon_commute(lookup)).value_or_none()
    if enabled is None or "lorena" in enabled:
        lorena = (await svc.commute_router.lorena_commute(lookup)).value_or_none()
    if enabled is None or "petrol" in enabled:
        petrol = (await svc.commute_router.petrol_cost(postcode)).value_or_none()

    # School enrichment defaults (may be overridden below)
    primary = None
    primary_commute = None
    primary_dist = None
    secondary = None
    secondary_commute = None
    secondary_dist = None

    if enabled is None or "schools" in enabled:
        loc_coords = location.coordinates.value_or_none()
        primary = await svc.school_lookup.find_nearest(
            postcode, child_age=7, address=address, requirement=SchoolGender.BOYS
        )
        primary_commute = await svc.school_lookup.school_commute(postcode, primary) if primary else None
        primary_dist = (
            round(loc_coords.distance_km_to(primary.coords), 2) if primary and primary.coords and loc_coords else None
        )
        secondary = await svc.school_lookup.find_nearest(
            postcode, child_age=12, address=address, requirement=SchoolGender.BOYS
        )
        secondary_commute = await svc.school_lookup.school_commute(postcode, secondary) if secondary else None
        secondary_dist = (
            round(loc_coords.distance_km_to(secondary.coords), 2)
            if secondary and secondary.coords and loc_coords
            else None
        )
    if enabled is None or {"walk_time", "amenities"} & enabled:
        coords = await resolve_house_location(
            postcode, address, actual_latitude, actual_longitude, approx_lat, approx_lng
        )
        if coords is not None:
            walk_data = await svc.walkability_service.enrich(coords.lat, coords.lon, address)
        else:
            walk_data = {"walk_to_town_minutes": None, "amenities": ""}
    if enabled is None or "town" in enabled:
        town_name = ""
        if address:
            parts = [p.strip() for p in address.split(",")]
            outcode_re = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$")
            postcode_re = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$", re.IGNORECASE)
            candidates = [p for p in parts if p and not postcode_re.match(p) and not outcode_re.match(p)]
            non_county = [p for p in candidates if p.lower().strip() not in KNOWN_COUNTIES]
            town_name = non_county[-1] if non_county else (candidates[-1] if candidates else "")
        town_desc = await svc.town_desc_service.describe(town_name, postcode)

    simon, lorena = await _enrich_rail_fares(enabled, postcode, address, simon, lorena)

    if simon and lorena and petrol and (enabled is None or {"simon", "lorena", "petrol"} & enabled):
        breakdown = await compute_commute_breakdown(simon, lorena, petrol)

    if enabled is None or "epc" in enabled:
        epc = await svc.epc_service.lookup(postcode, address) if postcode and not is_outcode(postcode) else ""

    if (enabled is None or "council_tax" in enabled) and postcode and not is_outcode(postcode) and address:
        result = await svc.council_tax_service.lookup(postcode, address)
        council_tax = result.value_or_none()
        if result.is_impossible:
            logger.debug("Council tax: %s for %s", result.reason, postcode)

    if enabled is None or "geo" in enabled:
        if actual_latitude is not None and actual_longitude is not None:
            approx_lat, approx_lng = actual_latitude, actual_longitude
        else:
            scraped_geo = await scrape_rightmove(url)
            if scraped_geo and scraped_geo.latitude is not None and scraped_geo.longitude is not None:
                approx_lat, approx_lng = scraped_geo.latitude, scraped_geo.longitude
            # else: approx_lat/lng already set from shared PropertyLocation above

        if approx_lat is not None and approx_lng is not None:
            station = get_rail_fare_registry().nearest_station(GeoPoint(approx_lat, approx_lng))
            if station:
                station_crs = station.crs
                station_name = station.name

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


async def run_backfill_enrichment(
    url: str,
    address: str,
    postcode: str,
    lookup: str,
    bedrooms: int | None,
    price: float | None,
    enabled: set[str] | None,
    services: Services | None = None,
) -> EnrichedProperty:
    """Backfill variant — thin wrapper around ``run_enrichment``.

    Exists as a separate entry point so tests can mock it independently
    from the primary enrichment flow.
    """
    return await run_enrichment(
        url=url,
        address=address,
        postcode=postcode,
        lookup=lookup,
        bedrooms=bedrooms,
        price=price,
        enabled=enabled,
        services=services,
    )
