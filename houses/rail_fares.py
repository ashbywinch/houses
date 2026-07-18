"""Rail fare data registry — lazy-loaded station and fare data.

``RailFareRegistry`` is a pure data registry with no enrichment logic.
It uses ``StationRegistry`` for station lookups (no duplicate CSV loading).
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from money import Money

from houses.commute import Commute, LegMode
from houses.config import settings
from houses.geo import GeoPoint
from houses.location import extract_postcode, geocode
from houses.stations import Station, StationRegistry
from houses.transit_route import FALLBACK_TUBE_SINGLE_GBP, get_tube_leg_fare

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RailFare:
    """A single fare between two stations."""

    origin_crs: str
    dest_crs: str
    single_fare_gbp: Money


class RailFareRegistry:
    """Lazy-loaded registry of rail fare data.

    Loads ``data/rail_fares.csv`` on first query and caches the result.
    Uses ``StationRegistry`` for station lookups (no duplicate CSV loading).
    No enrichment logic — pure data lookup.
    """

    def __init__(
        self,
        station_registry: StationRegistry | None = None,
        _fares_csv: Path | None = None,
    ):
        self._station_registry = station_registry or StationRegistry()
        self._fares_csv = _fares_csv or Path("data/rail_fares.csv")
        self._fares_by_pair: dict[frozenset[str], Money] | None = None

    def _load(self) -> None:
        """Parse the fares CSV into a lookup dict keyed by {origin_crs, dest_crs}."""
        if self._fares_by_pair is not None:
            return
        fares: dict[frozenset[str], Money] = {}
        if not self._fares_csv.is_file():
            logger.warning("Rail fares CSV not found at %s", self._fares_csv)
            self._fares_by_pair = fares
            return
        with self._fares_csv.open(newline="") as f:
            for row in csv.DictReader(f):
                origin = (row.get("origin_crs") or "").strip().upper()
                dest = (row.get("dest_crs") or "").strip().upper()
                cost_str = (row.get("single_fare_gbp") or "").strip()
                if origin and dest and cost_str:
                    try:
                        fares[frozenset({origin, dest})] = Money(cost_str, "GBP")
                    except Exception:
                        continue
        self._fares_by_pair = fares

    def nearest_station(self, point: GeoPoint) -> Station | None:
        """Return the station nearest to *point*."""
        return self._station_registry.nearest(point)

    def find_station_by_crs(self, crs: str) -> Station | None:
        """Look up a station by CRS code."""
        return self._station_registry.find_by_crs(crs)


    def find_station(self, name: str) -> Station | None:
        """Look up a station by name (suffix- and case-insensitive).

        Delegates to ``StationRegistry.find()`` — strips common suffixes
        like " Rail Station", " Station", etc.
        """
        return self._station_registry.find(name)
    def fare_between(self, origin: Station, destination: Station) -> Money | None:
        """Return the single fare between two stations.

        Tries exact origin→destination, then reverse (fares are symmetric
        for singles).  Returns ``None`` if no fare exists for this pair.
        """
        self._load()
        if not self._fares_by_pair:
            return None
        return self._fares_by_pair.get(frozenset({origin.crs, destination.crs}))


async def enrich_rail_fares(
    enabled: set[str] | None,
    postcode: str,
    address: str,
    simon: Commute,
    lorena: Commute,
    _registry: RailFareRegistry | None = None,
    _geocode=None,
    _tube_fare_fn=None,
) -> tuple[Commute, Commute]:
    """Fallback: look up National Rail fares when TfL didn't return a cost.

    .. deprecated::
       Use :func:`enrich_single_rail_fare` instead.  This function will be
       removed in a future release once all callers migrate to the
       station-object API.

    ``_registry`` — optional ``RailFareRegistry`` instance.
    ``_geocode`` — optional async geocode function.
    ``_tube_fare_fn`` — optional async tube fare function (default: ``get_tube_leg_fare``).
    """
    from houses.rail_fare_registry import get_rail_fare_registry

    geo_fn = _geocode or geocode
    registry = _registry or get_rail_fare_registry()
    tube_fare_fn = _tube_fare_fn or get_tube_leg_fare

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
                tube_fare = await tube_fare_fn(dest, settings.simon_postcode)
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
                tube_fare = await tube_fare_fn(dest, settings.lorena_postcode)
                tube_single = tube_fare or Money(FALLBACK_TUBE_SINGLE_GBP, "GBP")
                rail_cost = (fare + tube_single) * 2
                existing = lorena.daily_cost_gbp or Money("0", "GBP")
                total = rail_cost + existing
                lorena = Commute(
                    destination_label=lorena.destination_label,
                    destination_postcode=lorena.destination_postcode,
                    duration_minutes=lorena.duration_minutes,
                    daily_cost_gbp=total,
                )
                logger.info(
                    "NR fare fallback for Lorena: %s (rail) + %s (tube) = %s",
                    str(fare.amount),
                    str(tube_single.amount),
                    str(total.amount),
                )

    return simon, lorena


async def enrich_single_rail_fare(
    commute: Commute,
    origin_station: Station,
    dest_station: Station,
    destination_postcode: str,
    parking_cost: Money | None = None,
    _registry: RailFareRegistry | None = None,
    _tube_fare_fn=None,
) -> Commute | None:
    """Enrich a single commute with a National Rail fare.

    Looks up the fare between *origin_station* and *dest_station*,
    applies a tube fare for last-mile connectivity, adds any
    *parking_cost*, and returns an enriched ``Commute`` with
    ``daily_cost_gbp`` set.

    Returns ``None`` if no fare exists between the two stations.
    Returns the original *commute* unchanged (but with the fare applied)
    when the lookup succeeds.

    ``_registry`` — optional ``RailFareRegistry`` instance.
    ``_tube_fare_fn`` — optional async tube fare function (default: ``get_tube_leg_fare``).
    """
    from houses.rail_fare_registry import get_rail_fare_registry

    registry = _registry or get_rail_fare_registry()
    tube_fare_fn = _tube_fare_fn or get_tube_leg_fare

    fare = registry.fare_between(origin_station, dest_station)
    if fare is None:
        return None

    tube_fare = await tube_fare_fn(dest_station, destination_postcode)
    tube_single = tube_fare or Money(FALLBACK_TUBE_SINGLE_GBP, "GBP")
    rail_cost = (fare + tube_single) * 2

    total = rail_cost + parking_cost if parking_cost is not None else rail_cost

    enriched = Commute(
        destination_label=commute.destination_label,
        destination_postcode=commute.destination_postcode,
        duration_minutes=commute.duration_minutes,
        daily_cost_gbp=total,
    )

    logger.info(
        "NR fare: %s (rail) + %s (tube)%s = %s",
        str(fare.amount),
        str(tube_single.amount),
        f" + {parking_cost.amount} (parking)" if parking_cost else "",
        str(total.amount),
    )

    return enriched
