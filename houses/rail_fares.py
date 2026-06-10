import contextlib
import csv
import logging
from pathlib import Path

from houses.geo import GeoPoint

logger = logging.getLogger(__name__)

_STATIONS_CSV = Path("data/stations.csv")
_FARES_CSV = Path("data/rail_fares.csv")


def _load_stations() -> list[dict]:
    if not _STATIONS_CSV.is_file():
        logger.warning("Stations CSV not found at %s", _STATIONS_CSV)
        return []
    with _STATIONS_CSV.open(newline="") as f:
        return list(csv.DictReader(f))


def nearest_station(lat: float, lng: float) -> dict | None:
    stations = _load_stations()
    if not stations:
        return None
    origin = GeoPoint(lat, lng)
    best = None
    best_dist = float("inf")
    for s in stations:
        d = origin.distance_km_to(GeoPoint(float(s["lat"]), float(s["long"])))
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
    fares: dict[tuple[str, str], float] = {}
    if not _FARES_CSV.is_file():
        logger.warning("Rail fares CSV not found at %s", _FARES_CSV)
        return fares
    with _FARES_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            origin = row.get("origin_crs", "").strip().upper()
            dest = row.get("dest_crs", "").strip().upper()
            cost_str = row.get("single_fare_gbp", "").strip()
            if origin and dest and cost_str:
                with contextlib.suppress(ValueError):
                    fares[(origin, dest)] = float(cost_str)
    return fares


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
