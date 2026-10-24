"""Park-and-ride and drive-time helpers for transit route planning."""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import Field

from houses import apis
from serialization import SerializableAsDict, optional_parse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _LegMode:
    """A TfL journey leg mode — the {name} wire shape."""

    name: str | None

    @classmethod
    def from_dict(cls, raw: dict) -> _LegMode:
        return cls(name=raw.get("name"))


@dataclass(frozen=True)
class _Instruction:
    """A TfL journey leg instruction — the {summary} wire shape."""

    summary: str

    @classmethod
    def from_dict(cls, raw: dict) -> _Instruction:
        return cls(summary=raw.get("summary", ""))


@dataclass(frozen=True)
class _ArrivalPoint:
    """A TfL journey leg arrival point — the {commonName} wire shape.

    Holds the provider's raw row so the park-and-ride replacement leg
    writes it back verbatim (no key loss on the wire).
    """

    common_name: str
    raw: Mapping[str, Any]

    def to_dict(self) -> Mapping[str, Any]:
        return self.raw

    @classmethod
    def from_dict(cls, raw: dict) -> _ArrivalPoint:
        return cls(common_name=raw.get("commonName", ""), raw=dict(raw))


@dataclass(frozen=True)
class _FirstLeg:
    """The first leg of a TfL journey — the walk-to-station leg the
    park-and-ride swap inspects (mode, duration, arrival point)."""

    mode: _LegMode
    duration: int
    arrival_point: _ArrivalPoint | None

    @classmethod
    def from_dict(cls, raw: dict) -> _FirstLeg:
        return cls(
            mode=_LegMode.from_dict(raw.get("mode", {})),
            duration=raw.get("duration", 0),
            arrival_point=optional_parse(raw, "arrivalPoint", _ArrivalPoint.from_dict),
        )


class _DrivingLeg(SerializableAsDict):
    """The park-and-ride driving leg written into the TfL journeys payload."""

    mode: _LegMode
    duration: int
    instruction: _Instruction
    arrival_point: Mapping[str, Any] | None = Field(default=None, alias="arrivalPoint")


async def _get_drive_minutes(
    origin_postcode: str,
    station_name: str,
    *,
    services,
    _client_factory=None,
    _no_cache: bool = False,
) -> int | None:
    """Drive time from a postcode to a station.  The postcode is
    geocoded first — callers that already hold coordinates should use
    ``_get_drive_minutes_from_location`` and skip the lookup.

    ``services`` is REQUIRED and is the one code path: production threads
    the ``Services`` container (``_DefaultDriveTimeService``, park-and-ride),
    tests inject ``make_services`` fakes — no fallback branch exists to
    drift untested from production.
    """
    geocoder = services.geocoder
    origin_coords = (await geocoder.geocode_postcode(origin_postcode)).value_or_none()
    if origin_coords is None:
        origin_coords = (await geocoder.geocode_address(origin_postcode)).value_or_none()
    if origin_coords is None:
        return None
    return await _get_drive_minutes_from_location(
        origin_coords,
        station_name,
        _client_factory=_client_factory,
        _no_cache=_no_cache,
        services=services,
    )


async def _get_drive_minutes_from_location(
    origin_coords,
    station_name: str,
    *,
    services,
    _client_factory=None,
    _no_cache: bool = False,
) -> int | None:
    """Drive time from known coordinates to a station — the fallback
    when a property has no postcode but does have a best location."""
    station = services.station_lookup.find(station_name)
    dest_coords = station.location if station else None
    if dest_coords is None:
        dest_coords = (await services.geocoder.geocode_address(station_name)).value_or_none()
    if dest_coords is None:
        return None

    try:
        # None = quota exhausted or no route — the caller keeps the walk leg
        return await apis.ors.directions(
            origin_coords,
            dest_coords,
            mode="driving-car",
            _client_factory=_client_factory,
            _no_cache=_no_cache,
        )
    except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as exc:
        # Log and re-raise the ORIGINAL exception: _compute_attempt is the
        # single classifier (transient -> retry + pending, permanent ->
        # impossible). Wrapping in RuntimeError would hide the httpx type
        # and lose the retry decision (2026-09-19). This is the whole ORS
        # wire blast radius: HTTP statuses, transport errors, body JSON
        # parse failures — anything else is a bug and must surface.
        logger.warning(
            "Park-and-ride ORS lookup failed for %s -> %s: %s",
            origin_coords,
            station_name,
            exc,
        )
        raise


# The TfL journeys payload is the provider's own nested mapping: it carries
# keys no TypedDict can name (``$type``), so the swap types it as the mutable
# provider mapping it is — the one shape both ends of the seam agree on.
async def apply_park_and_ride_to_journeys(
    data: MutableMapping[str, Any],
    origin_postcode: str,
    max_walk_minutes: int,
    _drive_fn=None,
    services: Any | None = None,
) -> MutableMapping[str, Any]:
    if _drive_fn is not None:
        get_drive = _drive_fn
    else:
        if services is None:
            raise TypeError(
                "apply_park_and_ride_to_journeys needs services= for the real "
                "drive estimate (TflClient threads its container)"
            )

        async def get_drive(postcode: str, station: str) -> int | None:
            return await _get_drive_minutes(postcode, station, services=services)

    journeys = data.get("journeys", [])
    if not journeys:
        return data
    for journey in journeys:
        legs = journey.get("legs", [])
        if not legs:
            continue
        first = _FirstLeg.from_dict(legs[0])
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
        legs[0] = _DrivingLeg(
            mode=_LegMode(name="driving"),
            duration=drive_minutes,
            instruction=_Instruction(summary=f"Drive to {station_name}"),
            arrival_point=first.arrival_point.to_dict() if first.arrival_point else None,
        ).to_dict()
        old_duration = journey.get("duration", 0)
        journey["duration"] = old_duration - walk_duration + drive_minutes
    return data
