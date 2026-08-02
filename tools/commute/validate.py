"""Validation — geometry, coverage, and URL checks for the search set.

Run against ``searches.json`` + ``station_shed.json``; prints issues and exits
non-zero on any. The curated town controls are resolved against ``stations.csv``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from houses.geo import GeoPoint
from tools.commute.rightmove_url import parse_search_url
from tools.commute.station_shed import BBox
from tools.commute.tile import Rect, point_to_rect_distance_km

DEFAULT_SHED = Path("data/commute/station_shed.json")
DEFAULT_SEARCHES = Path("data/commute/searches.json")
MAX_RECTANGLES = 100
MAX_VERTICES = 20
COVERAGE_EPS_KM = 1e-6
NEGATIVE_EPS_KM = 0.5

# Curated commuter towns — every one must be covered by the search union.
POSITIVE_TOWNS = [
    "Reading", "Guildford", "Brighton", "Cambridge", "Milton Keynes Central",
    "Luton", "Bedford", "Peterborough", "Grantham", "Colchester",
    "Southend Victoria", "Maidstone East", "Sevenoaks", "Tunbridge Wells",
    "Ashford International", "Basingstoke", "Oxford", "High Wycombe",
    "Aylesbury", "Swindon", "Chelmsford", "Crawley", "Watford Junction",
    "St Albans", "Slough", "Northampton", "Ipswich",
    # Inner-London assertions — the inner zone must not hollow out.
    "Ealing Broadway", "Richmond", "East Croydon", "Bromley South", "Stratford (London)",
]
# Towns that must stay OUTSIDE the union (catch a keep-everything bug).
NEGATIVE_TOWNS = ["Exeter St Davids", "Sheffield"]


def _polygon_to_rect(poly: list[tuple[float, float]]) -> Rect:
    lats = [p[0] for p in poly]
    lons = [p[1] for p in poly]
    return Rect(min(lats), max(lats), min(lons), max(lons))


def _rects_overlap(a: Rect, b: Rect) -> bool:
    return not (
        a.lat_max <= b.lat_min
        or b.lat_max <= a.lat_min
        or a.lon_max <= b.lon_min
        or b.lon_max <= a.lon_min
    )


def _covered(rects: list[Rect], lat: float, lon: float, radius_km: float) -> bool:
    point = GeoPoint(lat, lon)
    return any(point_to_rect_distance_km(point, r) <= radius_km for r in rects)


def validate(
    payload: dict,
    kept_stations: list[dict],
    *,
    buffer_km: float,
    bbox: BBox,
    positive: list[tuple[str, float, float]] | None = None,
    negative: list[tuple[str, float, float]] | None = None,
    max_rectangles: int = MAX_RECTANGLES,
) -> list[str]:
    """Return a list of issues; empty means the search set passes."""
    issues: list[str] = []
    searches = payload["searches"]
    positive = positive or []
    negative = negative or []
    if len(searches) > max_rectangles:
        issues.append(f"{len(searches)} rectangles exceeds the limit of {max_rectangles}")

    rects: list[Rect] = []
    for s in searches:
        poly = s["polygon"]
        if len(poly) != 4:
            issues.append(f"{s['id']}: polygon has {len(poly)} points, expected 4")
        if len(poly) > MAX_VERTICES:
            issues.append(f"{s['id']}: polygon has {len(poly)} vertices, limit {MAX_VERTICES}")
        for lat, lon in poly:
            if not bbox.contains(lat, lon):
                issues.append(f"{s['id']}: vertex ({lat},{lon}) outside bounding box")
        try:
            parsed = parse_search_url(s["rightmove_url"])
            expected = poly + [poly[0]]
            if len(parsed) != len(expected) or any(
                abs(pl - el) > 5e-6 or abs(po - eo) > 5e-6
                for (pl, po), (el, eo) in zip(parsed, expected, strict=True)
            ):
                issues.append(f"{s['id']}: URL polygon round-trip mismatch")
        except (KeyError, ValueError, IndexError):
            issues.append(f"{s['id']}: URL polygon round-trip failed")
        rects.append(_polygon_to_rect(poly))

    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            if _rects_overlap(a, b):
                issues.append(f"searches {i + 1} and {i + 2} overlap")

    # Coverage contract: every kept station within buffer of some rectangle.
    for st in kept_stations:
        if not _covered(rects, st["lat"], st["lon"], buffer_km + COVERAGE_EPS_KM):
            issues.append(f"kept station {st['name']} ({st['crs']}) is not covered by any search")

    for name, lat, lon in positive:
        if not _covered(rects, lat, lon, buffer_km + COVERAGE_EPS_KM):
            issues.append(f"positive control {name} is not covered")

    for name, lat, lon in negative:
        if _covered(rects, lat, lon, NEGATIVE_EPS_KM):
            issues.append(f"negative control {name} is unexpectedly covered")

    return issues


def resolve_controls(names: list[str]) -> tuple[list[tuple[str, float, float]], list[str]]:
    """Resolve station names against stations.csv; returns (points, missing)."""
    from houses.stations import find as find_station

    points: list[tuple[str, float, float]] = []
    missing: list[str] = []
    for name in names:
        st = find_station(name)
        if st is None:
            missing.append(name)
        else:
            points.append((st.name, st.location.lat, st.location.lon))
    return points, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the Rightmove search set.")
    parser.add_argument("--shed", default=str(DEFAULT_SHED))
    parser.add_argument("--searches", default=str(DEFAULT_SEARCHES))
    parser.add_argument("--buffer-km", type=float, default=5.0)
    args = parser.parse_args(argv)

    shed_path, searches_path = Path(args.shed), Path(args.searches)
    if not shed_path.exists() or not searches_path.exists():
        print(
            f"need {shed_path} and {searches_path} — run 'make commute-shed' and 'make commute-searches'",
            file=sys.stderr,
        )
        return 1

    shed = json.loads(shed_path.read_text())
    payload = json.loads(searches_path.read_text())
    kept = [r for r in shed["stations"] if r["kept"]]

    positive, missing_pos = resolve_controls(POSITIVE_TOWNS)
    negative, missing_neg = resolve_controls(NEGATIVE_TOWNS)
    missing = [(name, "positive") for name in missing_pos] + [(name, "negative") for name in missing_neg]

    issues = validate(
        payload,
        kept,
        buffer_km=args.buffer_km,
        bbox=BBox(**shed["metadata"]["bbox"]),
        positive=positive,
        negative=negative,
    )
    for name, kind in missing:
        issues.append(f"{kind} control station {name!r} not found in stations.csv")

    for issue in issues:
        print(f"  ✗ {issue}")
    if issues:
        print(f"{len(issues)} validation issue(s) — fix before use")
        return 1
    print(f"✓ {len(payload['searches'])} searches: geometry, coverage, and controls pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
