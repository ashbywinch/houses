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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pint import Quantity

from houses.geopoint import GeoPoint
from tools.commute.rightmove_url import build_search_url
from tools.commute.station_shed import BBox
from tools.commute.tile import Grid, Rect, merge_rectangles, merge_rows, rasterize, rect_to_polygon
from tools.commute.union import union_outline
from tools.commute.units import KM

logger = logging.getLogger(__name__)

ENGINE_VERSION = "searches-v1"
DEFAULT_SHED = Path("data/commute/station_shed.json")
DEFAULT_OUT_DIR = Path("data/commute")
POLYLINE_DECIMALS = 5
DEFAULT_INTERSECTION_CELL_KM = 4.0  # same as the drive grids (drive_isochrone.DEFAULT_CELL_KM)
DEFAULT_INTERSECTION_CELL_KM_Q = DEFAULT_INTERSECTION_CELL_KM * KM


@dataclass(frozen=True)
class RasterOptions:
    """Grid rasterization + Rightmove filter policy shared by every shed→search builder."""

    cell_km: float
    buffer_km: float
    min_beds: int
    property_type: str


@dataclass(frozen=True)
class SearchOptions(RasterOptions):
    """Raster policy plus the payload metadata build_searches writes."""

    threshold_min: int
    destinations: list[str]
    generated_at: str
    engine_version: str


@dataclass(frozen=True)
class IntersectionOptions:
    """Build policy for one intersection payload: grid cell size, the Rightmove
    search filters, and the payload timestamp."""

    generated_at: str
    cell_km: Quantity = DEFAULT_INTERSECTION_CELL_KM_Q
    min_beds: int = 2
    property_type: str = "houses"


# Committed-payload wire shapes (coding-standards.md: serialized payloads use
# bare dicts by design) — named so signatures say which payload flows where.
SearchesPayload = dict[str, Any]
UnionPayload = dict[str, Any]
SearchRecord = dict[str, Any]


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def nearest_station_name(rect: Rect, kept_stations: list[dict]) -> str:
    """Human-readable name: the nearest kept station to the rectangle's centre."""
    centre = GeoPoint((rect.lat_min + rect.lat_max) / 2.0, (rect.lon_min + rect.lon_max) / 2.0)
    best = min(kept_stations, key=lambda s: centre.distance_km_to(GeoPoint(s["lat"], s["lon"])))
    return f"{best['name']} area"


def build_searches(
    rects: list[Rect],
    kept_stations: list[dict],
    options: SearchOptions,
) -> SearchesPayload:
    """Turn rectangles into the searches payload (deterministic given inputs)."""
    searches = []
    for i, rect in enumerate(rects, 1):
        # Round to the polyline encode precision (1e-5): grid-derived bounds
        # carry float noise (e.g. -0.8498050000000002) that the URL encoder
        # rounds away, so the JSON polygon must match the encoded form exactly
        # or the round-trip validation flakes at half-step boundaries.
        poly = [(round(p.lat, POLYLINE_DECIMALS), round(p.lon, POLYLINE_DECIMALS)) for p in rect_to_polygon(rect)]
        searches.append(  # lucidlint: ignore record-shape wire-format dict — the searches.json record
            {
                "id": f"s{i:03d}",
                "name": nearest_station_name(rect, kept_stations),
                "polygon": poly,
                # lucidlint: ignore record-shape wire-format dict — nested filters travel in the search record
                "filters": {"min_beds": options.min_beds, "property_type": options.property_type},
                "rightmove_url": build_search_url(poly, min_beds=options.min_beds, property_type=options.property_type),
            }
        )
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        "metadata": {
            "threshold_min": options.threshold_min,
            "destinations": options.destinations,
            "generated_at": options.generated_at,
            "engine_version": options.engine_version,
            "count": len(searches),
        },
        "searches": searches,
    }


def shed_to_searches(
    records: list[dict],
    bbox: BBox,
    options: SearchOptions,
) -> SearchesPayload:
    """Full pipeline: kept records → grid cells → rectangles → searches payload."""
    kept = [r for r in records if r["kept"]]
    grid = Grid.from_cell_km(Rect(bbox.lat_min, bbox.lat_max, bbox.lon_min, bbox.lon_max), options.cell_km)
    cells = rasterize([GeoPoint(r["lat"], r["lon"]) for r in kept], options.buffer_km, grid)
    rects = merge_rectangles(merge_rows(cells, grid))
    return build_searches(rects, kept, options)



# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _existing_searches(path: Path) -> dict | None:
    """Current searches.json payload, or None when absent/unreadable (will rewrite)."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        logger.warning("%s unreadable (corrupt?) — will rewrite", path)
        return None


# lucidlint: ignore latent-class (payload, out_dir) is the natural (what, where) of a write function — each writer
def write_searches(payload: SearchesPayload, out_dir: str | Path) -> None:
    """Write searches.json + .txt — but never churn an identical artifact.

    ``commute-validate`` regenerates searches as part of its gate; if only
    ``generated_at`` changed, rewriting would dirty the committed artifact on
    every validation run. The file is written only when the searches (or any
    metadata besides the timestamp) actually differ.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "searches.json"
    existing = _existing_searches(path)
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


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _urls_text(payload: dict) -> str:
    urls = [s["rightmove_url"] for s in payload["searches"]]
    return "\n".join(urls) + "\n"

def _same_searches(existing: SearchesPayload, new: SearchesPayload) -> bool:
    """Byte-identical apart from ``generated_at`` (the determinism contract).

    Compares JSON-serialized forms so tuple/list differences from the file
    round-trip (polygons) don't cause false churn.
    """
    if json.dumps(existing.get("searches"), sort_keys=True) != json.dumps(new.get("searches"), sort_keys=True):
        return False
    e_meta = {k: v for k, v in existing.get("metadata", {}).items() if k != "generated_at"}
    n_meta = {k: v for k, v in new.get("metadata", {}).items() if k != "generated_at"}
    return json.dumps(e_meta, sort_keys=True) == json.dumps(n_meta, sort_keys=True)


def union_payload(
    kept_stations: list[dict],
    bbox: BBox,
    options: RasterOptions,
) -> UnionPayload:
    """The shed as ONE-polygon-per-component Rightmove searches.

    Rightmove drawn-area searches take a single polygon. The shed is not one
    connected blob (station catchments around separate towns don't touch), so
    the union decomposes into connected components — each gets an outline and
    its own search URL covering that whole region.
    """

    grid = Grid.from_cell_km(Rect(bbox.lat_min, bbox.lat_max, bbox.lon_min, bbox.lon_max), options.cell_km)
    cells = rasterize([GeoPoint(r["lat"], r["lon"]) for r in kept_stations], options.buffer_km, grid)
    components = [
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        {
            "outline": [[p.lat, p.lon] for p in loop],
            "rightmove_url": build_search_url(
                [(p.lat, p.lon) for p in loop],
                min_beds=options.min_beds,
                property_type=options.property_type,
            ),
        }
        for loop in union_outline(cells, grid)
    ]
    return {"components": components}


def write_union(payload: UnionPayload, out_dir: str | Path) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "union.json").write_text(json.dumps(payload, indent=2) + "\n")


def write_map_html(payload: UnionPayload, searches: list[SearchRecord], out_dir: str | Path) -> None:
    """Self-contained Leaflet map: every search rectangle + the component outlines.

    Each rectangle is a layer whose popup links to its Rightmove search; the
    outlines show each connected region's boundary. Tiles from OSM (internet).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rects_js = []
    for s in searches:
        coords = [[lat, lon] for lat, lon in s["polygon"]]
        # lucidlint: ignore record-shape wire-format dict — popup record embedded in the map HTML (coding-standards.md)
        rects_js.append(json.dumps({"name": s["name"], "url": s["rightmove_url"], "coords": coords}))
    outlines_js = [[[lat, lon] for lat, lon in c["outline"]] for c in payload["components"]]
    html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Commute search coverage</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body{margin:0;height:100%}#map{height:100%}</style></head>
<body><div id="map"></div>
<script>
const rects = __RECTS__;
const outlines = __OUTLINES__;
const map = L.map('map');
if (rects.length) { map.fitBounds(L.latLngBounds(rects.flatMap(r => r.coords))); }
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 18}).addTo(map);
for (const o of outlines) {
  L.polygon(o, {color: '#d33', weight: 3, fill: false}).addTo(map);
}
for (const r of rects) {
  L.polygon(r.coords, {color: '#3388ff', weight: 1, fillOpacity: 0.15})
    .addTo(map)
    .bindPopup('<b>' + r.name + '</b><br><a href="' + r.url + '" target="_blank">Rightmove search</a>');
}
</script></body></html>
"""
    html = html.replace("__RECTS__", "[" + ",".join(rects_js) + "]").replace("__OUTLINES__", repr(outlines_js))
    (out_dir / "searches.html").write_text(html)


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
    search_options = SearchOptions(
        cell_km=args.cell_km,
        buffer_km=args.buffer_km,
        min_beds=args.min_beds,
        property_type=args.property_type,
        threshold_min=metadata["threshold_min"],
        destinations=metadata["destinations"],
        generated_at=datetime.now(UTC).isoformat(),
        engine_version=ENGINE_VERSION,
    )
    payload = shed_to_searches(shed["stations"], BBox(**metadata["bbox"]), search_options)
    write_searches(payload, args.out_dir)
    kept = [r for r in shed["stations"] if r["kept"]]
    union = union_payload(
        kept,
        BBox(**metadata["bbox"]),
        RasterOptions(
            cell_km=search_options.cell_km,
            buffer_km=search_options.buffer_km,
            min_beds=search_options.min_beds,
            property_type=search_options.property_type,
        ),
    )
    write_union(union, args.out_dir)
    write_map_html(union, payload["searches"], args.out_dir)
    print(f"{len(payload['searches'])} searches → {Path(args.out_dir) / 'searches.json'}")
    print(f"union: {len(union['components'])} component outline(s) → {Path(args.out_dir) / 'union.json'}")
    print(f"map → {Path(args.out_dir) / 'searches.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
