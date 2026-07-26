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
from pint import Quantity

from houses.geo import GeoPoint
from houses.model.domain import Commute, PlaceOfInterest
from houses.stations import Station, StationRegistry
from houses.tfl_client import TflClient

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
    ``daily_cost`` set.

    Returns ``None`` if no fare exists between the two stations.
    Returns the original *commute* (but with the fare applied)
    when the lookup succeeds.

    ``_registry`` — optional ``RailFareRegistry`` instance.
    ``_tube_fare_fn`` — optional async tube fare function (default: ``get_tube_leg_fare``).
    """
    from houses.rail_fare_registry import get_rail_fare_registry

    registry = _registry or get_rail_fare_registry()
    tube_fare_fn = _tube_fare_fn or TflClient.get_tube_leg_fare

    fare = registry.fare_between(origin_station, dest_station)
    if fare is None:
        return None

    tube_fare = await tube_fare_fn(dest_station, destination_postcode)
    tube_single = tube_fare or Money(TflClient.FALLBACK_TUBE_SINGLE_GBP, "GBP")
    rail_cost = (fare + tube_single) * 2

    total = rail_cost + parking_cost if parking_cost is not None else rail_cost

    enriched = Commute(
        person=commute.person,
        label=commute.label,
        destination=PlaceOfInterest(
            label=commute.destination.label,
            address=commute.destination.address,
        ),
        duration=Quantity(int(commute.duration.magnitude), "minute") if commute.duration else Quantity(0, "minute"),
        daily_cost=total,
        details=commute.details,
        mode=commute.mode,
        is_child=commute.is_child,
    )

    logger.info(
        "NR fare: %s (rail) + %s (tube)%s = %s",
        str(fare.amount),
        str(tube_single.amount),
        f" + {parking_cost.amount} (parking)" if parking_cost else "",
        str(total.amount),
    )

    return enriched
