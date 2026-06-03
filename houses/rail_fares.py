import contextlib
import csv
import logging
import math
from pathlib import Path

logger = logging.getLogger(__name__)

_STATIONS_CSV = Path("data/stations.csv")
_FARES_CSV = Path("data/rail_fares.csv")

_stations: list[dict] | None = None
_fares: dict[tuple[str, str], float] | None = None


def _load_stations() -> list[dict]:
    global _stations
    if _stations is not None:
        return _stations
    if not _STATIONS_CSV.is_file():
        logger.warning("Stations CSV not found at %s", _STATIONS_CSV)
        _stations = []
        return _stations
    with _STATIONS_CSV.open(newline="") as f:
        _stations = list(csv.DictReader(f))
    logger.info("Loaded %d stations from %s", len(_stations), _STATIONS_CSV)
    return _stations


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def nearest_station(lat: float, lng: float) -> dict | None:
    stations = _load_stations()
    if not stations:
        return None
    best = None
    best_dist = float("inf")
    for s in stations:
        d = _haversine_km(lat, lng, float(s["lat"]), float(s["long"]))
        if d < best_dist:
            best_dist = d
            best = s
    if best is None:
        return None
    return {
        "name": best["stationName"],
        "crs": best["crsCode"],
        "distance_km": round(best_dist, 2),
    }


def _load_fares() -> dict[tuple[str, str], float]:
    global _fares
    if _fares is not None:
        return _fares
    _fares = {}
    if not _FARES_CSV.is_file():
        logger.warning("Rail fares CSV not found at %s", _FARES_CSV)
        return _fares
    with _FARES_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            origin = row.get("origin_crs", "").strip().upper()
            dest = row.get("dest_crs", "").strip().upper()
            cost_str = row.get("single_fare_gbp", "").strip()
            if origin and dest and cost_str:
                with contextlib.suppress(ValueError):
                    _fares[(origin, dest)] = float(cost_str)
    logger.info("Loaded %d fare records from %s", len(_fares), _FARES_CSV)
    return _fares


LONDON_CRS = {"VIC", "FST", "PAD", "WAT", "WAE", "EUS", "LST", "STP", "KGX", "LBG", "CST", "CHX", "BFR", "SRA", "LON"}


def fare_between(origin_crs: str, dest_crs: str) -> float | None:
    fares = _load_fares()
    origin = origin_crs.strip().upper()
    dest = dest_crs.strip().upper()

    # Try exact match
    cost = fares.get((origin, dest))
    if cost is not None:
        return cost

    # Try reverse (fares are typically symmetric for singles)
    cost = fares.get((dest, origin))
    if cost is not None:
        return cost

    # If destination is a London terminal, try cheapest London terminal
    if dest in LONDON_CRS:
        best = None
        for ldn_crs in LONDON_CRS:
            cost = fares.get((origin, ldn_crs))
            if cost is not None and (best is None or cost < best):
                best = cost
        if best is not None:
            return best

    return None
