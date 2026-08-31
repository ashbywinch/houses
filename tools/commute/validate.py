"""Validation — geometry, coverage, and URL checks for the search set.

Run against ``searches.json`` + ``station_shed.json``; prints issues and exits
non-zero on any. The curated town controls are resolved against ``stations.csv``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from houses.geopoint import GeoPoint
from houses.stations import find as find_station
from tools.commute.rightmove_url import parse_search_url
from tools.commute.station_shed import BBox
from tools.commute.tile import Grid, Rect, point_to_rect_distance_km, rasterize


@dataclass(frozen=True)
class StationControl:
    """A curated town assertion: the station's name and coordinates."""

    name: str
    point: GeoPoint

logger = logging.getLogger(__name__)

DEFAULT_SHED = Path("data/commute/station_shed.json")
DEFAULT_SEARCHES = Path("data/commute/searches.json")
MAX_RECTANGLES = 100
MAX_VERTICES = 20
COVERAGE_EPS_KM = 1e-6
NEGATIVE_EPS_KM = 0.5
ROUND_TRIP_EPS = 5e-6
RECT_VERTEX_COUNT = 4
MAX_REPORTED_UNCOVERED = 10


@dataclass(frozen=True)
class ValidationOptions:
    """The search-set validation policy: coverage buffer, bounding box, the
    curated positive/negative controls, and the rectangle cap."""

    buffer_km: float
    bbox: BBox
    positive: list[StationControl] | None = None
    negative: list[StationControl] | None = None
    max_rectangles: int = MAX_RECTANGLES


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


def _polygon_to_rect(poly: list[GeoPoint]) -> Rect:
    lats = [p.lat for p in poly]
    lons = [p.lon for p in poly]
    return Rect(min(lats), max(lats), min(lons), max(lons))


def _rects_overlap(a: Rect, b: Rect) -> bool:
    return not (
        a.lat_max <= b.lat_min
        or b.lat_max <= a.lat_min
        or a.lon_max <= b.lon_min
        or b.lon_max <= a.lon_min
    )


def _covered(rects: list[Rect], point: GeoPoint, radius_km: float) -> bool:
    return any(point_to_rect_distance_km(point, r) <= radius_km for r in rects)


def _point_in_rect(point: GeoPoint, rect: Rect) -> bool:
    return rect.lat_min <= point.lat <= rect.lat_max and rect.lon_min <= point.lon <= rect.lon_max


# lucidlint: ignore record-shape kept_stations ride in the committed searches payload's station shape
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def uncovered_cells(  # lucidlint: ignore record-shape returns a list of (row, col) grid lattice coordinates — keyed
    searches: list[dict],
    kept_stations: list[dict],
    bbox: BBox,
    *,
    cell_km: float,
    buffer_km: float,
) -> list[tuple[int, int]]:
    """Kept cells not inside any search rectangle — regeneration-drift guard.

    searches.json and union.json are generated from the same keep-set in one
    run, but a stale artifact or partial regeneration would leave a
    gate-passing house in a cell no search covers. Assert the inverse: every
    kept cell's centre lies inside some search polygon.
    """

    grid = Grid.from_cell_km(Rect(bbox.lat_min, bbox.lat_max, bbox.lon_min, bbox.lon_max), cell_km)
    cells = rasterize([GeoPoint(r["lat"], r["lon"]) for r in kept_stations], buffer_km, grid)
    rects = [_polygon_to_rect([GeoPoint(lat, lon) for lat, lon in s["polygon"]]) for s in searches]
    uncovered: list[tuple[int, int]] = []
    for cell in cells:
        centre = GeoPoint(cell.lat, cell.lon)
        if not any(_point_in_rect(centre, rect) for rect in rects):
            uncovered.append((cell.row, cell.col))
    return uncovered


# lucidlint: ignore record-shape [lat, lon] vertex pairs — GeoPoint is the record; a pair wrapper is ceremony
def _rect_from_poly(poly: list[tuple[float, float]]) -> Rect:
    """Bounding rect of a polygon's [lat, lon] vertex list."""
    return _polygon_to_rect([GeoPoint(lat, lon) for lat, lon in poly])


def _search_issues(sid: str, poly, url: str, bbox: BBox) -> list[str]:
    """Geometry, bbox, and URL round-trip issues for one search rectangle."""
    issues: list[str] = []
    if len(poly) != RECT_VERTEX_COUNT:
        issues.append(f"{sid}: polygon has {len(poly)} points, expected 4")
    if len(poly) > MAX_VERTICES:
        issues.append(f"{sid}: polygon has {len(poly)} vertices, limit {MAX_VERTICES}")
    issues.extend(
        f"{sid}: vertex ({lat},{lon}) outside bounding box"
        for lat, lon in poly
        if not bbox.contains(lat, lon)
    )
    try:
        parsed = parse_search_url(url)
        expected = poly + [poly[0]]
        if len(parsed) != len(expected) or any(
            abs(pl - el) > ROUND_TRIP_EPS or abs(po - eo) > ROUND_TRIP_EPS
            for (pl, po), (el, eo) in zip(parsed, expected, strict=True)
        ):
            issues.append(f"{sid}: URL polygon round-trip mismatch")
    except (KeyError, ValueError, IndexError):
        issues.append(f"{sid}: URL polygon round-trip failed")
    return issues


def _overlap_issues(rects: list[Rect]) -> list[str]:
    """Every pair of overlapping search rectangles."""
    return [
        f"searches {i + 1} and {i + 2} overlap"
        for i, a in enumerate(rects)
        for b in rects[i + 1 :]
        if _rects_overlap(a, b)
    ]


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _coverage_issues(rects: list[Rect], kept_stations: list[dict], buffer_km: float, positive, negative) -> list[str]:
    """Coverage contract: kept stations and positive controls covered, negative controls not."""
    issues: list[str] = []
    issues.extend(
        f"kept station {st['name']} ({st['crs']}) is not covered by any search"
        for st in kept_stations
        if not _covered(rects, GeoPoint(st["lat"], st["lon"]), buffer_km + COVERAGE_EPS_KM)
    )
    issues.extend(
        f"positive control {c.name} is not covered"
        for c in positive
        if not _covered(rects, c.point, buffer_km + COVERAGE_EPS_KM)
    )
    issues.extend(
        f"negative control {c.name} is unexpectedly covered"
        for c in negative
        if _covered(rects, c.point, NEGATIVE_EPS_KM)
    )
    return issues


# lucidlint: ignore record-shape kept_stations ride in the committed searches payload's station shape
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def validate(
    payload: dict,
    kept_stations: list[dict],
    options: ValidationOptions,
) -> list[str]:
    """Return a list of issues; empty means the search set passes."""
    issues: list[str] = []
    searches = payload["searches"]
    positive = options.positive or []
    negative = options.negative or []
    if len(searches) > options.max_rectangles:
        issues.append(f"{len(searches)} rectangles exceeds the limit of {options.max_rectangles}")

    rects: list[Rect] = []
    for s in searches:
        poly = s["polygon"]
        issues.extend(_search_issues(s["id"], poly, s["rightmove_url"], options.bbox))
        rects.append(_rect_from_poly(poly))

    issues.extend(_overlap_issues(rects))
    issues.extend(_coverage_issues(rects, kept_stations, options.buffer_km, positive, negative))
    return issues


@dataclass(frozen=True)
class ResolvedControls:
    """Control stations resolved against stations.csv, plus any names it lacks."""

    controls: list[StationControl]
    missing: list[str]


def resolve_controls(names: list[str]) -> ResolvedControls:
    """Resolve station names against stations.csv."""

    points: list[StationControl] = []
    missing: list[str] = []
    for name in names:
        st = find_station(name)
        if st is None:
            missing.append(name)
        else:
            points.append(StationControl(name=st.name, point=GeoPoint(st.location.lat, st.location.lon)))
    return ResolvedControls(points, missing)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the Rightmove search set.")
    parser.add_argument("--shed", default=str(DEFAULT_SHED))
    parser.add_argument("--searches", default=str(DEFAULT_SEARCHES))
    parser.add_argument("--buffer-km", type=float, default=5.0)
    parser.add_argument("--cell-km", type=float, default=8.0)
    args = parser.parse_args(argv)

    shed_path, searches_path = Path(args.shed), Path(args.searches)
    if not shed_path.exists() or not searches_path.exists():
        logger.warning(
            "missing inputs: shed=%s exists=%s, searches=%s exists=%s",
            shed_path,
            shed_path.exists(),
            searches_path,
            searches_path.exists(),
        )
        print(
            f"need {shed_path} and {searches_path} — run 'make commute-shed' and 'make commute-searches'",
            file=sys.stderr,
        )
        return 1

    shed = json.loads(shed_path.read_text())
    payload = json.loads(searches_path.read_text())
    kept = [r for r in shed["stations"] if r["kept"]]

    positive = resolve_controls(POSITIVE_TOWNS)
    negative = resolve_controls(NEGATIVE_TOWNS)
    missing = [(name, "positive") for name in positive.missing] + [(name, "negative") for name in negative.missing]

    issues = validate(
        payload,
        kept,
        ValidationOptions(
            buffer_km=args.buffer_km,
            bbox=BBox(**shed["metadata"]["bbox"]),
            positive=positive.controls,
            negative=negative.controls,
        ),
    )
    for name, kind in missing:
        logger.warning(
            "control station %r (%s) not found in stations.csv — check the POSITIVE/NEGATIVE_TOWNS lists", name, kind
        )
        issues.append(f"{kind} control station {name!r} not found in stations.csv")

    uncovered = uncovered_cells(
        payload["searches"],
        kept,
        BBox(**shed["metadata"]["bbox"]),
        cell_km=args.cell_km,
        buffer_km=args.buffer_km,
    )
    issues.extend(
        f"kept cell ({r},{c}) is not covered by any search rectangle"
        for r, c in uncovered[:MAX_REPORTED_UNCOVERED]
    )
    if len(uncovered) > MAX_REPORTED_UNCOVERED:
        issues.append(f"... and {len(uncovered) - MAX_REPORTED_UNCOVERED} more uncovered cells")

    for issue in issues:
        print(f"  ✗ {issue}")
    if issues:
        print(f"{len(issues)} validation issue(s) — fix before use")
        return 1
    print(f"✓ {len(payload['searches'])} searches: geometry, coverage, and controls pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
