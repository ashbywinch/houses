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

from houses.geo import GeoPoint
from houses.stations import Station, StationRegistry

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

    def fare_between(self, origin: Station, destination: Station) -> Money | None:
        """Return the single fare between two stations.

        Tries exact origin→destination, then reverse (fares are symmetric
        for singles).  Returns ``None`` if no fare exists for this pair.
        No London-terminal fallback — different terminals have different fares.
        """
        self._load()
        if not self._fares_by_pair:
            return None
        return self._fares_by_pair.get(frozenset({origin.crs, destination.crs}))
