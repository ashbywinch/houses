"""Intersection — where EVERY commute works, i.e. where to buy a house.

ANDs the toolchain's sheds on one common grid:

- the **transit shed** (Pimlico & Aldgate) — the same station-catchment
  predicate the transit toolchain rasterizes (kept stations within 5 km);
- **every driving destination's shed** (Dad, Bracknell, …) — cell centres
  inside the committed drive-search polygons.

A house in the intersection is commutable to an office by rail AND within the
drive threshold of every driving destination. The result is traced with the
same `union_outline` machinery and emitted as one drawn-area Rightmove search
(``data/commute/intersection.json``), plus a layer on the combined map.

The common grid is 4 km over the overlap of the driving regions — cells
outside any drive shed can never qualify, so the overlap bounds the search.
Deterministic and fully offline: ``make commute-intersection`` regenerates
from the committed payloads.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pint import Quantity

from houses.geopoint import GeoPoint
from tools.commute.drive_isochrone import (
    GB_BBOX,
    grid_cell_centers,
    outer_loop,
    point_in_polygon,
    region_bbox,
    retained_components,
)
from tools.commute.rightmove_url import build_search_url, parse_search_url
from tools.commute.searches import IntersectionOptions
from tools.commute.tile import Grid, GridCell, Rect, rasterize
from tools.commute.units import KM

logger = logging.getLogger(__name__)

ENGINE_VERSION = "intersection-v1"
DEFAULT_SHED = Path("data/commute/station_shed.json")
DEFAULT_DRIVE_RAW = Path("data/commute/drive_isochrone.json")
DEFAULT_DRIVE_SEARCHES = Path("data/commute/drive_searches.json")
DEFAULT_OUT = Path("data/commute/intersection.json")
DEFAULT_CELL_KM = 4.0  # same as the drive grids
TRANSIT_BUFFER_KM = 5.0  # same catchment radius the transit shed rasterizes
MAX_VERTICES = 500
POLYLINE_DECIMALS = 5
MIN_POLYGON_VERTICES = 4
ROUND_TRIP_EPS = 5e-6  # degree tolerance for URL polygon round-trip comparison

DEFAULT_CELL_KM_Q = DEFAULT_CELL_KM * KM
DEFAULT_BUFFER_KM_Q = TRANSIT_BUFFER_KM * KM


def common_grid(drive_raw: dict, cell_km: Quantity = DEFAULT_CELL_KM_Q) -> Grid:
    """One grid over the overlap of the driving regions.

    The intersection is a subset of every drive region, so cells outside the
    overlap can never qualify — the overlap bbox bounds the search.
    ``cell_km`` is a pint Quantity; payload distances are bare numbers (the
    wire format), restored to units here.
    """
    region_km = drive_raw["metadata"]["region_km"] * KM
    boxes = [region_bbox(d["lat"], d["lon"], region_km) for d in drive_raw["destinations"]]
    if not boxes:
        raise ValueError("no drive destinations — nothing to intersect")
    bbox = Rect(
        lat_min=max(b.lat_min for b in boxes),
        lat_max=min(b.lat_max for b in boxes),
        lon_min=max(b.lon_min for b in boxes),
        lon_max=min(b.lon_max for b in boxes),
    )
    if bbox.lat_min >= bbox.lat_max or bbox.lon_min >= bbox.lon_max:
        raise ValueError("drive regions do not overlap — the intersection is empty")
    return Grid.from_cell_km(bbox, cell_km.to("km").magnitude)


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def transit_cells(
    kept_stations: list[dict], grid: Grid, buffer_km: Quantity = DEFAULT_BUFFER_KM_Q
) -> set[GridCell]:
    """Cells whose nearest point is within ``buffer_km`` of a kept station.

    Exactly the predicate the transit toolchain rasterizes (``tile.rasterize``)
    — a kept cell means a house in it is within the walk/bus-to-station radius.
    """
    return rasterize([GeoPoint(s["lat"], s["lon"]) for s in kept_stations], buffer_km.to("km").magnitude, grid)


# lucidlint: ignore record-shape shed polygons — homogeneous point collections, not field-wise records
def drive_cells(polygons: list[list[GeoPoint]], grid: Grid) -> set[GridCell]:
    """Cells whose centre lies inside any of the destination's shed polygons."""
    if not polygons:
        return set()
    cells = grid_cell_centers(grid)
    return {cell for cell in cells if any(point_in_polygon(cell.lat, cell.lon, p) for p in polygons)}


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _group_drive_polygons(drive_searches: dict) -> dict[str, list[list[GeoPoint]]]:
    """Shed polygons per drive destination (a shed may be several loops)."""
    by_label: dict[str, list[list[GeoPoint]]] = {}
    # lucidlint: ignore loop-pipeline group-by accumulation — no comprehension form; groupby would re-sort
    for s in drive_searches.get("searches", []):
        by_label.setdefault(s["destination"]["label"], []).append(
            [GeoPoint(lat, lon) for lat, lon in s["polygon"]]
        )
    return by_label


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _missing_destinations(drive_raw: dict, by_label: dict) -> list[str]:
    """Drive destinations absent from the searches, raising on stale drive data.

    A destination with no shed makes its constraint unsatisfiable: the
    intersection must be EMPTY, never silently dropped ("every commute works"
    would be a lie). The reverse check — drive_searches containing a
    destination ABSENT from the config — means stale committed data: ANDing it
    would silently narrow the intersection; the user must regenerate the drive
    data.
    """
    missing = [d["label"] for d in drive_raw["destinations"] if d["label"] not in by_label]
    stale = sorted(set(by_label) - {d["label"] for d in drive_raw["destinations"]})
    if stale:
        raise ValueError(
            f"stale drive data: destination(s) {', '.join(stale)} are not in the config — run 'make commute-drive'"
        )
    return missing


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _intersection_searches(
    kept: set[GridCell], grid: Grid, thresholds: list[int], min_beds: int, property_type: str
) -> list[dict]:
    """Kept cells → outlined polygon searches.

    The island filter (main shed always kept) is the drive toolchain's rule —
    one implementation, shared, so tuning it can't diverge.
    """
    searches: list[dict] = []
    for i, comp in enumerate(retained_components(kept), 1):
        loop = outer_loop(comp, grid)
        if loop is None:
            continue
        poly = [(round(p.lat, POLYLINE_DECIMALS), round(p.lon, POLYLINE_DECIMALS)) for p in loop]
        suffix = "" if i == 1 else f"-{i}"
        searches.append(
            {
                "id": f"intersection-{min(thresholds):03d}{suffix}",
                "name": "All commutes",
                "polygon": poly,
                "filters": {"min_beds": min_beds, "property_type": property_type},
                "rightmove_url": build_search_url(poly, min_beds=min_beds, property_type=property_type),
                "threshold_min": min(thresholds),
            }
        )
    return searches


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def build_payload(
    *,
    shed: dict,
    drive_raw: dict,
    drive_searches: dict,
    options: IntersectionOptions | None = None,
) -> dict:
    """Transit ∩ every drive shed → the intersection payload (deterministic)."""
    options = options or IntersectionOptions(generated_at="")
    cell_km, min_beds, property_type = options.cell_km, options.min_beds, options.property_type
    grid = common_grid(drive_raw, cell_km)
    kept_stations = [s for s in shed["stations"] if s.get("kept")]
    transit = transit_cells(kept_stations, grid)

    by_label = _group_drive_polygons(drive_searches)
    missing = _missing_destinations(drive_raw, by_label)
    if missing:
        logger.warning(
            "destination(s) have no shed — the all-commutes intersection is empty: %s", ", ".join(missing)
        )
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        return {
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            "metadata": {
                "engine_version": ENGINE_VERSION,
                "profile": "tfl-transit + driving-car",
                "threshold_min": min(d["threshold_min"] for d in drive_raw["destinations"]),
                "cell_km": cell_km.to("km").magnitude,
                "transit_buffer_km": TRANSIT_BUFFER_KM,
                "sources": ["station_shed.json", "drive_isochrone.json", "drive_searches.json"],
                "generated_at": options.generated_at,
                "count": 0,
            },
            "searches": [],
        }
    drive_sets = [drive_cells(polys, grid) for polys in by_label.values()]
    kept = transit
    for ds in drive_sets:
        kept &= ds

    thresholds = [d["threshold_min"] for d in drive_raw["destinations"]]
    searches = _intersection_searches(kept, grid, thresholds, min_beds, property_type)
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        "metadata": {
            "engine_version": ENGINE_VERSION,
            "profile": "tfl-transit + driving-car",
            "threshold_min": min(thresholds),
            "cell_km": cell_km.to("km").magnitude,
            "transit_buffer_km": TRANSIT_BUFFER_KM,
            "sources": ["station_shed.json", "drive_isochrone.json", "drive_searches.json"],
            "generated_at": options.generated_at,
            "count": len(searches),
        },
        "searches": searches,
    }


def _valid_polygon(poly) -> bool:
    """A polygon is a list of 2-element numeric [lat, lon] pairs."""
    return isinstance(poly, list) and all(
        isinstance(p, (list, tuple))
        and len(p) == 2
        and isinstance(p[0], (int, float))
        and isinstance(p[1], (int, float))
        for p in poly
    )


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _search_issues(s: dict, max_vertices: int) -> list[str]:
    """Polygon geometry, GB-bbox, and URL round-trip issues for one search."""
    issues: list[str] = []
    poly: Any = s.get("polygon")
    if not _valid_polygon(poly):
        issues.append(f"{s.get('id')}: polygon is malformed (expected [lat, lon] pairs)")
        return issues
    if len(poly) < MIN_POLYGON_VERTICES:
        issues.append(f"{s.get('id')}: polygon has {len(poly)} vertices (need ≥ 4)")
    elif poly[0] == poly[-1]:
        issues.append(f"{s.get('id')}: polygon is closed — loops must stay open")
    if len(poly) > max_vertices:
        issues.append(f"{s.get('id')}: {len(poly)} vertices exceeds the {max_vertices} cap")
    issues.extend(
        f"{s.get('id')}: vertex ({lat}, {lon}) outside the GB bounding box"
        for lat, lon in poly
        if not GB_BBOX.contains(lat, lon)
    )
    try:
        parsed = parse_search_url(s.get("rightmove_url", ""))
        expected = poly + [poly[0]]
        if len(parsed) != len(expected) or any(
            abs(pl - el) > ROUND_TRIP_EPS or abs(po - eo) > ROUND_TRIP_EPS
            for (pl, po), (el, eo) in zip(parsed, expected, strict=True)
        ):
            issues.append(f"{s.get('id')}: URL polygon does not round-trip to the stored polygon")
    # lucidlint: ignore broad-except any parse failure is a URL issue (mirrors the noqa BLE001)
    except Exception as e:  # noqa: BLE001 — any parse failure is a URL issue
        issues.append(f"{s.get('id')}: rightmove_url unparseable ({e})")
    return issues


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def validate_payload(payload: dict, *, max_vertices: int = MAX_VERTICES) -> list[str]:
    """Issues with the intersection payload; empty means it passes."""
    if not isinstance(payload, dict):
        return ["payload is malformed (expected an object)"]
    if "searches" not in payload:
        return ["'searches' is missing"]
    searches = payload["searches"]
    if not isinstance(searches, list):
        return ["'searches' is malformed (expected a list, got " + type(searches).__name__ + ")"]
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return ["'metadata' is malformed (expected an object)"]
    issues: list[str] = []
    if metadata.get("count") != len(searches):
        issues.append(f"metadata count {metadata.get('count')} != {len(searches)} searches")
    issues.extend(issue for s in searches for issue in _search_issues(s, max_vertices))
    return issues


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _same_payload(existing: dict, new: dict) -> bool:
    """Byte-identical apart from ``generated_at`` (the determinism contract)."""
    if not isinstance(existing, dict) or not isinstance(new, dict):
        return False
    if json.dumps(existing.get("searches"), sort_keys=True) != json.dumps(new.get("searches"), sort_keys=True):
        return False
    if not isinstance(existing.get("metadata"), dict) or not isinstance(new.get("metadata"), dict):
        return False
    e_meta = {k: v for k, v in existing["metadata"].items() if k != "generated_at"}
    n_meta = {k: v for k, v in new["metadata"].items() if k != "generated_at"}
    return json.dumps(e_meta, sort_keys=True) == json.dumps(n_meta, sort_keys=True)


def _fail(user_message: str, dev_detail: str) -> int:
    """Two-tier fail-fast exit (docs/coding-standards.md): a plain-language
    stderr line plus a logger.warning with the exact resolution."""
    print(user_message, file=sys.stderr)
    logger.warning(dev_detail)
    return 1


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _existing_payload(out_path: Path) -> dict | None:
    """Current payload, or None when absent/unreadable (will regenerate)."""
    if not out_path.exists():
        return None
    try:
        return json.loads(out_path.read_text())
    except json.JSONDecodeError:
        logger.warning("%s unreadable (corrupt?) — will regenerate", out_path)
        return None



def _validate_committed(out_path: Path) -> int:
    """--validate: check the committed intersection payload and report."""
    if not out_path.exists():
        return _fail(
            "No all-commutes data yet — run 'make commute-intersection' first.",
            f"{out_path} not found for --validate",
        )
    try:
        payload = json.loads(out_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return _fail(
            "The saved all-commutes data is unreadable — regenerate it with 'make commute-intersection'.",
            f"unreadable {out_path} for --validate: {e}",
        )
    issues = validate_payload(payload)
    if issues:
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print(f"intersection OK ({len(payload['searches'])} search(es))")
    return 0


# lucidlint: ignore class-module small private helper — module keeps its function name
@dataclass(frozen=True)
class _CommittedInputs:
    """The three committed payloads the map needs."""

    shed: dict
    raw: dict
    searches: dict


def _load_inputs(args) -> _CommittedInputs | int:
    """Read the three committed payloads; int exit code when any is missing/unreadable."""
    missing = [(p, hint) for p, hint in (
        (Path(args.shed), "make commute-shed"),
        (Path(args.drive_raw), "make commute-drive"),
        (Path(args.drive_searches), "make commute-drive"),
    ) if not Path(p).exists()]
    if missing:
        hints = list(dict.fromkeys(h for _, h in missing))
        return _fail(
            f"Commute data is missing — run {' and '.join(hints)} first.",
            f"intersection inputs not found: {', '.join(str(p) for p, _ in missing)}",
        )
    try:
        shed = json.loads(Path(args.shed).read_text())
        drive_raw = json.loads(Path(args.drive_raw).read_text())
        drive_searches = json.loads(Path(args.drive_searches).read_text())
    except (json.JSONDecodeError, OSError) as e:
        return _fail(
            "Commute data is unreadable — regenerate it with 'make commute-drive'.",
            f"unreadable intersection input: {e}",
        )
    return _CommittedInputs(shed=shed, raw=drive_raw, searches=drive_searches)


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _write_if_changed(out_path: Path, payload: dict) -> None:
    """Write the payload atomically, skipping a byte-identical committed copy."""
    existing = _existing_payload(out_path)
    if existing is None or not _same_payload(existing, payload):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp, out_path)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the all-commutes intersection search (offline).")
    parser.add_argument("--shed", default=str(DEFAULT_SHED))
    parser.add_argument("--drive-raw", default=str(DEFAULT_DRIVE_RAW))
    parser.add_argument("--drive-searches", default=str(DEFAULT_DRIVE_SEARCHES))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--cell-km", type=float, default=DEFAULT_CELL_KM)
    parser.add_argument("--min-beds", type=int, default=2)
    parser.add_argument("--property-type", default="houses")
    parser.add_argument("--validate", action="store_true", help="validate the committed intersection.json and exit")
    args = parser.parse_args(argv)

    out_path = Path(args.out)
    if args.validate:
        return _validate_committed(out_path)

    loaded = _load_inputs(args)
    if isinstance(loaded, int):
        return loaded
    shed, drive_raw, drive_searches = loaded.shed, loaded.raw, loaded.searches

    try:
        payload = build_payload(
            shed=shed,
            drive_raw=drive_raw,
            drive_searches=drive_searches,
            options=IntersectionOptions(
                generated_at=datetime.now(UTC).isoformat(),
                cell_km=args.cell_km * KM,
                min_beds=args.min_beds,
                property_type=args.property_type,
            ),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return _fail(
            "Can't build the all-commutes area — check the car destinations and try again.",
            f"intersection build failed: {e}",
        )
    if not payload["searches"]:
        print("warning: no intersection — no place satisfies every commute", file=sys.stderr)
    issues = validate_payload(payload)
    if issues:
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        print("intersection FAIL validation — nothing written", file=sys.stderr)
        return 1
    _write_if_changed(out_path, payload)
    print(f"{len(payload['searches'])} intersection search(es) → {out_path}")
    for s in payload["searches"]:
        print(f"  {s['id']}: {s['rightmove_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
