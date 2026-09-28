"""Park-and-ride and drive-time helpers for transit route planning."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from houses import apis
from houses.location import geocode, geocode_address
from houses.stations import find as find_station
from houses.web.json_utils import optional_parse

logger = logging.getLogger(__name__)



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
        return cls(
            mode=_LegModeJson.from_dict(raw.get("mode", {})),
            duration=raw.get("duration", 0),
            arrival_point=optional_parse(raw, "arrivalPoint", _ArrivalPointJson.from_dict),
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


async def _get_drive_minutes(
    origin_postcode: str, station_name: str, *, _client_factory=None
) -> int | None:
    """Drive time from a postcode to a station.  The postcode is
    geocoded first — callers that already hold coordinates should use
    ``_get_drive_minutes_from_location`` and skip the lookup."""
    origin_coords = (await geocode(origin_postcode)).value_or_none()
    if origin_coords is None:
        origin_coords = (await geocode_address(origin_postcode)).value_or_none()
    if origin_coords is None:
        return None
    return await _get_drive_minutes_from_location(
        origin_coords, station_name, _client_factory=_client_factory
    )


async def _get_drive_minutes_from_location(
    origin_coords, station_name: str, *, _client_factory=None
) -> int | None:
    """Drive time from known coordinates to a station — the fallback
    when a property has no postcode but does have a best location."""
    station = find_station(station_name)
    dest_coords = station.location if station else None
    if dest_coords is None:
        dest_coords = (await geocode_address(station_name)).value_or_none()
    if dest_coords is None:
        return None

    try:
        # None = quota exhausted or no route — the caller keeps the walk leg
        return await apis.ors.directions(
            origin_coords, dest_coords, mode="driving-car",
            _client_factory=_client_factory,
        )
    except Exception as exc:
        # Log and re-raise the ORIGINAL exception: _compute_attempt is the
        # single classifier (transient -> retry + pending, permanent ->
        # impossible). Wrapping in RuntimeError would hide the httpx type
        # and lose the retry decision (2026-09-19).
        logger.warning(
            "Park-and-ride ORS lookup failed for %s -> %s: %s",
            origin_coords,
            station_name,
            exc,
        )
        raise

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
