"""Search set — turn the shed + tiling into Rightmove search URLs (JSON + txt).

Reads ``data/commute/station_shed.json``, rasterizes the kept stations' catchments
onto a grid, row-merges into rectangles, and emits ``searches.json`` (the phase-2
scraper input) plus ``searches.txt`` (one URL per line for manual entry). Fully
offline — no live API calls.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from houses.geo import GeoPoint
from tools.commute.rightmove_url import build_search_url
from tools.commute.station_shed import BBox
from tools.commute.tile import Grid, Rect, merge_rectangles, merge_rows, rasterize, rect_to_polygon

logger = logging.getLogger(__name__)

ENGINE_VERSION = "searches-v1"
DEFAULT_SHED = Path("data/commute/station_shed.json")
DEFAULT_OUT_DIR = Path("data/commute")


def nearest_station_name(rect: Rect, kept_stations: list[dict]) -> str:
    """Human-readable name: the nearest kept station to the rectangle's centre."""
    centre = GeoPoint((rect.lat_min + rect.lat_max) / 2.0, (rect.lon_min + rect.lon_max) / 2.0)
    best = min(kept_stations, key=lambda s: centre.distance_km_to(GeoPoint(s["lat"], s["lon"])))
    return f"{best['name']} area"


def build_searches(
    rects: list[Rect],
    kept_stations: list[dict],
    *,
    threshold_min: int,
    destinations: list[str],
    min_beds: int,
    property_type: str,
    generated_at: str,
    engine_version: str,
) -> dict:
    """Turn rectangles into the searches payload (deterministic given inputs)."""
    searches = []
    for i, rect in enumerate(rects, 1):
        # Round to the polyline encode precision (1e-5): grid-derived bounds
        # carry float noise (e.g. -0.8498050000000002) that the URL encoder
        # rounds away, so the JSON polygon must match the encoded form exactly
        # or the round-trip validation flakes at half-step boundaries.
        poly = [(round(lat, 5), round(lon, 5)) for lat, lon in rect_to_polygon(rect)]
        searches.append(
            {
                "id": f"s{i:03d}",
                "name": nearest_station_name(rect, kept_stations),
                "polygon": poly,
                "filters": {"min_beds": min_beds, "property_type": property_type},
                "rightmove_url": build_search_url(poly, min_beds=min_beds, property_type=property_type),
            }
        )
    return {
        "metadata": {
            "threshold_min": threshold_min,
            "destinations": destinations,
            "generated_at": generated_at,
            "engine_version": engine_version,
            "count": len(searches),
        },
        "searches": searches,
    }


def shed_to_searches(
    records: list[dict],
    bbox: BBox,
    *,
    cell_km: float,
    buffer_km: float,
    min_beds: int,
    property_type: str,
    generated_at: str,
    engine_version: str,
    threshold_min: int,
    destinations: list[str],
) -> dict:
    """Full pipeline: kept records → grid cells → rectangles → searches payload."""
    kept = [r for r in records if r["kept"]]
    grid = Grid.from_cell_km(Rect(bbox.lat_min, bbox.lat_max, bbox.lon_min, bbox.lon_max), cell_km)
    cells = rasterize([(r["lat"], r["lon"]) for r in kept], buffer_km, grid)
    rects = merge_rectangles(merge_rows(cells, grid))
    return build_searches(
        rects,
        kept,
        threshold_min=threshold_min,
        destinations=destinations,
        min_beds=min_beds,
        property_type=property_type,
        generated_at=generated_at,
        engine_version=engine_version,
    )


def write_searches(payload: dict, out_dir: str | Path) -> None:
    """Write searches.json + .txt — but never churn an identical artifact.

    ``commute-validate`` regenerates searches as part of its gate; if only
    ``generated_at`` changed, rewriting would dirty the committed artifact on
    every validation run. The file is written only when the searches (or any
    metadata besides the timestamp) actually differ.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "searches.json"
    existing: dict | None = None
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError:
            existing = None
    if existing is not None and _same_searches(existing, payload):
        # JSON is current — but the txt must mirror it too (it can be missing
        # or stale from a partial write/checkout).
        txt_path = out_dir / "searches.txt"
        expected = _urls_text(payload)
        if not txt_path.exists() or txt_path.read_text() != expected:
            txt_path.write_text(expected)
        return
    path.write_text(json.dumps(payload, indent=2) + "\n")
    (out_dir / "searches.txt").write_text(_urls_text(payload))


def _urls_text(payload: dict) -> str:
    urls = [s["rightmove_url"] for s in payload["searches"]]
    return "\n".join(urls) + "\n"


def _same_searches(existing: dict, new: dict) -> bool:
    """Byte-identical apart from ``generated_at`` (the determinism contract).

    Compares JSON-serialized forms so tuple/list differences from the file
    round-trip (polygons) don't cause false churn.
    """
    if json.dumps(existing.get("searches"), sort_keys=True) != json.dumps(new.get("searches"), sort_keys=True):
        return False
    e_meta = {k: v for k, v in existing.get("metadata", {}).items() if k != "generated_at"}
    n_meta = {k: v for k, v in new.get("metadata", {}).items() if k != "generated_at"}
    return json.dumps(e_meta, sort_keys=True) == json.dumps(n_meta, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Rightmove search set from the station shed.")
    parser.add_argument("--shed", default=str(DEFAULT_SHED))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--cell-km", type=float, default=8.0, help="grid cell size in km (default 8)")
    parser.add_argument("--buffer-km", type=float, default=5.0, help="station catchment radius in km (default 5)")
    parser.add_argument("--min-beds", type=int, default=2)
    parser.add_argument("--property-type", default="houses")
    args = parser.parse_args(argv)

    shed_path = Path(args.shed)
    if not shed_path.exists():
        logger.warning("shed file missing at %s — searches cannot be built without it", shed_path)
        print(f"{shed_path} not found — run 'make commute-shed' first", file=sys.stderr)
        return 1
    try:
        shed = json.loads(shed_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("shed at %s is unreadable (%s) — regenerate with 'make commute-shed'", shed_path, e)
        print(f"{shed_path} is corrupt or unreadable — regenerate with 'make commute-shed'", file=sys.stderr)
        return 1
    metadata = shed["metadata"]
    payload = shed_to_searches(
        shed["stations"],
        BBox(**metadata["bbox"]),
        cell_km=args.cell_km,
        buffer_km=args.buffer_km,
        min_beds=args.min_beds,
        property_type=args.property_type,
        generated_at=datetime.now(UTC).isoformat(),
        engine_version=ENGINE_VERSION,
        threshold_min=metadata["threshold_min"],
        destinations=metadata["destinations"],
    )
    write_searches(payload, args.out_dir)
    print(f"{len(payload['searches'])} searches → {Path(args.out_dir) / 'searches.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
