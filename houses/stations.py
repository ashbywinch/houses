"""Station data — railway/Tube stations with name, CRS, and location.

Uses the existing ``GeoPoint`` for coordinates. The registry loads
``data/stations.csv`` lazily and caches the result as a dict of
``Station`` objects keyed by cleaned name and CRS code.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from houses.geo import GeoPoint

logger = logging.getLogger(__name__)

_STATIONS_CSV = Path("data/stations.csv")


@dataclass
class Station:
    """A railway or Tube station."""

    name: str  # canonical name from stations.csv, e.g. "Paddington"
    crs: str  # three-letter CRS code, e.g. "PAD"
    location: GeoPoint

    # Suffixes stripped by ``short_name()`` — order matters because
    # " Rail Station" is a suffix of " Underground Station".
    _STATION_SUFFIXES = [" Rail Station", " Underground Station", " Rail Station", " Station"]

    @staticmethod
    def short_name(raw: str) -> str:
        """Display-friendly station name for route summaries.

        Strips common suffixes (" Rail Station", " Underground Station")
        and the "London " prefix so the name reads naturally:

        >>> Station.short_name("Maidenhead Rail Station")
        'Maidenhead'
        >>> Station.short_name("London Paddington Rail Station")
        'Paddington'
        """
        for suffix in Station._STATION_SUFFIXES:
            if raw.endswith(suffix):
                raw = raw[: -len(suffix)]
                break
        if raw.startswith("London "):
            raw = raw[7:]
        return raw

    @property
    def short(self) -> str:
        """This station's display-friendly name."""
        return Station.short_name(self.name)


class StationRegistry:
    """Lazy-loaded registry of all UK stations from ``data/stations.csv``.

    Loads the CSV on first query and caches the result.  Lookups are
    case-insensitive and suffix-insensitive (strips " Rail Station",
    " Underground Station", " Station").
    """

    def __init__(self, _stations_csv: Path | None = None) -> None:
        self._stations: dict[str, Station] | None = None
        self._by_crs: dict[str, Station] | None = None
        self._csv_path = _stations_csv or _STATIONS_CSV

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_name(name: str) -> str:
        """Strip station suffixes for CSV matching.  Keeps "London " prefix."""
        name = name.replace("'", "").replace("\u2019", "")
        lower = name.lower()
        for suffix in [" rail station", " underground station", " station"]:
            if lower.endswith(suffix):
                name = name[: -len(suffix)]
                break
        return name.strip()

    def _load(self) -> None:
        """Parse ``data/stations.csv`` into lookup dicts."""
        if self._stations is not None:
            return
        stations: dict[str, Station] = {}
        by_crs: dict[str, Station] = {}
        if not self._csv_path.is_file():
            logger.warning("Stations CSV not found at %s", self._csv_path)
            self._stations = stations
            self._by_crs = by_crs
            return
        with self._csv_path.open(newline="") as f:
            for row in csv.DictReader(f):
                raw_name = (row.get("stationName") or "").strip()
                crs = (row.get("crsCode") or "").strip().upper()
                if not raw_name or not crs:
                    continue
                try:
                    lat = float(row.get("lat", ""))
                    lng = float(row.get("long", ""))
                except (ValueError, TypeError):
                    continue
                station = Station(name=raw_name, crs=crs, location=GeoPoint(lat=lat, lon=lng))
                key = self._clean_name(raw_name).lower()
                stations[key] = station
                by_crs[crs] = station
        self._stations = stations
        self._by_crs = by_crs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find(self, name: str) -> Station | None:
        """Look up a station by name (suffix- and case-insensitive).

        ``"Maidenhead Rail Station"``, ``"maidenhead rail station"``,
        and ``"Maidenhead"`` all match the same station.
        """
        self._load()
        cleaned = self._clean_name(name).lower()
        return self._stations.get(cleaned) if self._stations else None  # type: ignore[return-value]

    def find_by_crs(self, crs: str) -> Station | None:
        """Look up a station by CRS code (case-insensitive)."""
        self._load()
        return self._by_crs.get(crs.upper()) if self._by_crs else None  # type: ignore[return-value]

    def nearest(self, point: GeoPoint) -> Station | None:
        """Return the station nearest to *point*."""
        self._load()
        if not self._stations:
            return None
        best = None
        best_dist = float("inf")
        for station in self._stations.values():
            d = point.distance_km_to(station.location)
            if d < best_dist:
                best_dist = d
                best = station
        return best


# Module-level convenience — single shared instance.
_registry = StationRegistry()


def find(name: str) -> Station | None:
    """Convenience: look up a station by name."""
    return _registry.find(name)


def find_by_crs(crs: str) -> Station | None:
    """Convenience: look up a station by CRS code."""
    return _registry.find_by_crs(crs)
