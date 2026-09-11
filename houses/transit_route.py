"""Park-and-ride and drive-time helpers for transit route planning."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from houses.api_cache import cached_async_client, get_cached, set_cached
from houses.location import geocode, geocode_address
from houses.settings import settings
from houses.stations import find as find_station

logger = logging.getLogger(__name__)

OUTCODES_IO_URL = "https://api.postcodes.io/outcodes"
POSTCODES_IO_URL = "https://api.postcodes.io/postcodes"
ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
SECONDS_PER_MINUTE = 60


@dataclass(frozen=True)
class _DirectionsBodyJson:
    """The ORS directions request body — POSTed to openrouteservice."""

    coordinates: list[list[float]]
    units: str

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the ORS request body shape (coding-standards.md)
        return dict(coordinates=self.coordinates, units=self.units)


@dataclass(frozen=True)
class _DirectionsResponseJson:
    """The ORS directions response root — the {routes} wire shape."""

    routes: list[_DirectionsRouteJson]

    @classmethod
    def from_dict(cls, raw: dict) -> _DirectionsResponseJson:
        return cls(routes=[_DirectionsRouteJson.from_dict(route) for route in raw["routes"]])


@dataclass(frozen=True)
class _DirectionsRouteJson:
    """An ORS directions route — the {summary} wire shape."""

    summary: _DirectionsSummaryJson

    @classmethod
    def from_dict(cls, raw: dict) -> _DirectionsRouteJson:
        return cls(summary=_DirectionsSummaryJson.from_dict(raw["summary"]))


@dataclass(frozen=True)
class _DirectionsSummaryJson:
    """The ORS route summary — the {duration} wire shape."""

    duration: float

    @classmethod
    def from_dict(cls, raw: dict) -> _DirectionsSummaryJson:
        return cls(duration=raw["duration"])


@dataclass(frozen=True)
class _LegModeJson:
    """A TfL journey leg mode — the {name} wire shape."""

    name: str | None

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return dict(name=self.name)

    @classmethod
    def from_dict(cls, raw: dict) -> _LegModeJson:
        return cls(name=raw.get("name"))


@dataclass(frozen=True)
class _InstructionJson:
    """A TfL journey leg instruction — the {summary} wire shape."""

    summary: str

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return dict(summary=self.summary)


@dataclass(frozen=True)
class _ArrivalPointJson:
    """A TfL journey leg arrival point — the {commonName} wire shape.

    Holds the provider's raw dict so the park-and-ride replacement leg
    writes back the exact same object (no key loss on the wire).
    """

    common_name: str
    raw: dict[str, Any]

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return self.raw

    @classmethod
    def from_dict(cls, raw: dict) -> _ArrivalPointJson:
        return cls(common_name=raw.get("commonName", ""), raw=raw)


@dataclass(frozen=True)
class _FirstLegJson:
    """The first leg of a TfL journey — the walk-to-station leg the
    park-and-ride swap inspects (mode, duration, arrival point)."""

    mode: _LegModeJson
    duration: int
    arrival_point: _ArrivalPointJson | None

    @classmethod
    def from_dict(cls, raw: dict) -> _FirstLegJson:
        arrival = raw.get("arrivalPoint")
        return cls(
            mode=_LegModeJson.from_dict(raw.get("mode", {})),
            duration=raw.get("duration", 0),
            arrival_point=_ArrivalPointJson.from_dict(arrival) if arrival else None,
        )


@dataclass(frozen=True)
class _DrivingLegJson:
    """The park-and-ride driving leg written into the TfL journeys payload."""

    mode: _LegModeJson
    duration: int
    instruction: _InstructionJson
    arrival_point: Any

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the TfL journeys leg shape (coding-standards.md)
        return dict(
            mode=self.mode.to_dict(),
            duration=self.duration,
            instruction=self.instruction.to_dict(),
            arrivalPoint=self.arrival_point,
        )


async def _get_drive_minutes(origin_postcode: str, station_name: str) -> int | None:
    """Drive time from a postcode to a station.  The postcode is
    geocoded first — callers that already hold coordinates should use
    ``_get_drive_minutes_from_location`` and skip the lookup."""
    origin_coords = (await geocode(origin_postcode)).value_or_none()
    if origin_coords is None:
        origin_coords = (await geocode_address(origin_postcode)).value_or_none()
    if origin_coords is None:
        return None
    return await _get_drive_minutes_from_location(origin_coords, station_name)


async def _get_drive_minutes_from_location(origin_coords, station_name: str) -> int | None:
    """Drive time from known coordinates to a station — the fallback
    when a property has no postcode but does have a best location."""
    station = find_station(station_name)
    dest_coords = station.location if station else None
    if dest_coords is None:
        dest_coords = (await geocode_address(station_name)).value_or_none()
    if dest_coords is None:
        return None

    dest_lat = dest_coords.lat
    dest_lng = dest_coords.lon

    body = _DirectionsBodyJson(
        coordinates=[[origin_coords.lon, origin_coords.lat], [dest_lng, dest_lat]],
        units="km",
    )
    payload = body.to_dict()
    key = json.dumps(payload, sort_keys=True)
    try:
        async with cached_async_client(timeout=15.0) as client:
            cached = get_cached("POST", ORS_DIRECTIONS_URL, None, key)
            if cached is not None:
                response = _DirectionsResponseJson.from_dict(cached)
                return round(response.routes[0].summary.duration / SECONDS_PER_MINUTE)
            resp = await client.post(
                ORS_DIRECTIONS_URL,
                headers={"Authorization": settings.ors_api_key, "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            set_cached("POST", ORS_DIRECTIONS_URL, None, key, data)
            response = _DirectionsResponseJson.from_dict(data)
            return round(response.routes[0].summary.duration / SECONDS_PER_MINUTE)
    # lucidlint: ignore broad-except ORS park-and-ride lookup logs and falls back
    except Exception:
        logger.warning(
            "Park-and-ride ORS lookup failed for %s \u2192 %s (url=%s)",
            origin_coords,
            station_name,
            ORS_DIRECTIONS_URL,
        )
        return None


# lucidlint: ignore record-shape consumes the TfL journeys provider payload — provider wire shape (coding-standards.md)
# lucidlint: ignore record-shape returns the mutated TfL journeys payload — provider wire shape (coding-standards.md)
async def apply_park_and_ride_to_journeys(
    data: dict,
    origin_postcode: str,
    max_walk_minutes: int,
    _drive_fn=None,
) -> dict:
    get_drive = _drive_fn if _drive_fn is not None else _get_drive_minutes
    journeys = data.get("journeys", [])
    if not journeys:
        return data
    for journey in journeys:
        legs = journey.get("legs", [])
        if not legs:
            continue
        first = _FirstLegJson.from_dict(legs[0])
        if first.mode.name != "walking":
            continue
        walk_duration = first.duration
        logger.debug(
            "park_and_ride: walk leg=%dm to station='%s' threshold=%dm",
            walk_duration,
            first.arrival_point.common_name if first.arrival_point else "?",
            max_walk_minutes,
        )
        if walk_duration <= max_walk_minutes:
            logger.debug(
                "park_and_ride: walk %dm <= %dm threshold \u2014 keeping walk", walk_duration, max_walk_minutes
            )
            continue
        station_name = first.arrival_point.common_name if first.arrival_point else ""
        if not station_name:
            logger.debug("park_and_ride: walk leg has no arrivalPoint \u2014 skipping")
            continue
        drive_minutes = await get_drive(origin_postcode, station_name)
        if drive_minutes is None:
            logger.debug(
                "park_and_ride: ORS returned None for '%s' -> '%s' \u2014 keeping walk",
                origin_postcode,
                station_name,
            )
            continue
        logger.debug(
            "park_and_ride: replacing walk %dm with drive %dm to '%s'",
            walk_duration,
            drive_minutes,
            station_name,
        )
        legs[0] = _DrivingLegJson(
            mode=_LegModeJson(name="driving"),
            duration=drive_minutes,
            instruction=_InstructionJson(summary=f"Drive to {station_name}"),
            arrival_point=first.arrival_point.to_dict() if first.arrival_point else None,
        ).to_dict()
        old_duration = journey.get("duration", 0)
        journey["duration"] = old_duration - walk_duration + drive_minutes
    return data

