"""Drive isochrones — one-off ORS matrix batch producing driving search URLs.

Companion to the transit shed toolchain (``station_shed.py`` → ``searches.py``),
for destinations reached by car. It answers the same question by a different
route: instead of routing every station to the office, it routes a GRID of
points to each driving destination and keeps the cells whose drive time fits
the threshold. The kept-cell set flows through the same tiling/outline/URL
machinery as the transit shed, so the outputs are structurally identical
Rightmove drawn-area search URLs.

Engine choice, why matrix and not the isochrone endpoint: the hosted
OpenRouteService API caps ``/isochrones`` ranges at 3600 s (60 min) — a
1.5 h bound is impossible there. ``/matrix`` has no such cap (it routes, it
does not contour) and returns one duration per grid point in a single
request, so the isochrone is built by thresholding durations instead of by
asking for a contour. One matrix call handles ~1000 grid points; the whole
batch is a handful of polite requests, disk-cached (``data/api_cache/``) so
re-runs are offline.

Speeds are ORS ``driving-car`` free-flow — no traffic model (see
docs/rightmove-commute-monitor.md for why that is accepted). The per-property
gate uses Google Routes with live traffic, so over-coverage at the boundary
is the safe direction and the gate is the accuracy layer.

Outputs (committed, like the transit artifacts):
- ``drive_isochrone.json`` — raw durations per grid cell per destination
  (the reproducibility artifact; ``--force`` regenerates it).
- ``drive_searches.json`` / ``.txt`` — one drawn-area search per destination
  (per connected component of its shed), scraper-compatible shape.
- ``drive_searches.html`` — Leaflet map for eyeballing coverage.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
import math
import os
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pint import Quantity

from houses.api_cache import cached_async_client
from houses.geopoint import GeoPoint
from houses.location import geocode
from houses.settings import settings
from tools.commute.rightmove_url import build_search_url, parse_search_url
from tools.commute.station_shed import DEFAULT_BBOX, BBox
from tools.commute.tile import KM_PER_DEG_LAT, Grid, GridCell, Rect
from tools.commute.union import union_outline
from tools.commute.units import KM, KMH, MINUTE

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class MapAssets:
    """The vendored Leaflet assets embedded into the page: JS/CSS sources and
    filename → data-URI icon map."""

    leaflet_js: str
    leaflet_css: str
    icons: dict[str, str]


# one colour per layer — the transit layer always takes _COLORS[0], so the
# drive palette excludes it (a drive shed must never look like the train shed)
_COLORS = ["#e33", "#3a3", "#e80", "#a3a", "#0aa"]
_DRIVE_COLORS = _COLORS[1:]


def js_safe_json(obj) -> str:
    """JSON safe to embed inside an HTML <script> element.

    json.dumps does not escape ``<``/``>``/``&``, so a user-controlled label
    like ``</script><script>…`` would terminate the script element. Escape
    them as unicode escapes (still valid JSON; JS decodes them back).
    """
    return json.dumps(obj).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def user_label(label: str) -> str:
    """HTML-escape user-controlled labels: they render via innerHTML in the
    layer control and marker popups."""
    return html.escape(label)

ORS_MATRIX_URL = "https://api.openrouteservice.org/v2/matrix/driving-car"
ENGINE_VERSION = "drive-isochrone-v1"
SEARCHES_VERSION = "drive-searches-v1"
DEFAULT_CONFIG = Path("data/commute/drive_destinations.json")
DEFAULT_OUT_DIR = Path("data/commute")
RAW_FILENAME = "drive_isochrone.json"
SEARCHES_FILENAME = "drive_searches.json"
TXT_FILENAME = "drive_searches.txt"
MAP_FILENAME = "drive_searches.html"
DEFAULT_CELL_KM = 4.0
DEFAULT_THRESHOLD_MIN = 90
REGION_MULTIPLIER = 1.7  # region radius (km) = threshold_min * this — covers the free-flow frontier
MAX_LOCATIONS_PER_REQUEST = 1000  # ORS matrix cap (incl. the destination) — probed live
FREEFLOW_KMH = 70.0  # slack = half-cell crossing time at this speed
MAX_VERTICES = 500
MIN_ISLAND_CELLS = 4  # fringe speckles below this many cells are noise
GB_BBOX = BBox(**DEFAULT_BBOX)  # sanity bound for polygon vertices

_REQUEST_DELAY_S = 0.2  # polite sequential cadence between matrix calls
CELL_COUNT_EPSILON = 1e-9  # float-error guard before ceil — exact multiples must not add a phantom row
SECONDS_PER_MINUTE = 60.0
MINUTES_PER_HOUR = 60.0
MINUTES_DECIMALS = 3
POLYLINE_DECIMALS = 5
MIN_POLYGON_VERTICES = 4
MIN_CONTAINMENT_VERTICES = 3
ROUND_TRIP_EPS = 5e-6  # degree tolerance for URL polygon round-trip comparison


@dataclass(frozen=True)
class DriveDestination:
    label: str
    postcode: str
    threshold_min: Quantity


def load_config(path: str | Path, default_threshold: int = DEFAULT_THRESHOLD_MIN) -> list[DriveDestination]:
    """Parse the destinations config file.

    Shape: ``{"threshold_min": 90, "destinations": [{"label": "Dad",
    "postcode": "OX7 5GZ", "threshold_min": 120}]}`` — the top-level
    ``threshold_min`` is the default for every destination; a per-destination
    value overrides it. Destinations are DATA, not code — adding a POI means
    editing this file, nothing else. Config values are bare minutes (the wire
    format); thresholds become Quantities in memory.
    """
    data = json.loads(Path(path).read_text())
    threshold = int(data.get("threshold_min", default_threshold))
    destinations: list[DriveDestination] = []
    for entry in data.get("destinations", []):
        label = entry.get("label")
        postcode = entry.get("postcode")
        if not label or not postcode:
            raise ValueError(f"each destination needs 'label' and 'postcode', got {entry!r}")
        destinations.append(
            DriveDestination(
                label=label, postcode=postcode, threshold_min=int(entry.get("threshold_min", threshold)) * MINUTE
            )
        )
    if not destinations:
        raise ValueError("drive destinations config has no destinations")
    labels = [d.label for d in destinations]
    dupes = sorted({label for label in labels if labels.count(label) > 1})
    if dupes:
        raise ValueError(f"duplicate destination labels: {', '.join(dupes)}")
    return destinations


def apply_default_threshold(
    destinations: list[DriveDestination], config_data: dict, threshold_min: Quantity
) -> list[DriveDestination]:
    """Apply a CLI default threshold to every destination that lacks an
    explicit per-destination override in the config file — the documented
    meaning of "override the config's default threshold".

    ``load_config``'s own ``default_threshold`` cannot express this: the
    file's top-level ``threshold_min`` would win over the CLI value.
    """
    overridden = {d["label"] for d in config_data.get("destinations", []) if "threshold_min" in d}
    return [
        replace(d, threshold_min=threshold_min) if d.label not in overridden else d for d in destinations
    ]


# ── geometry ─────────────────────────────────────────────────────────


def slack_minutes(cell_km: Quantity) -> Quantity:
    """Boundary slack: the time to cross a half cell diagonal at free-flow speed.

    A kept cell's centre is within threshold, but a house in that cell can be
    up to a half-diagonal further from the destination. Keeping cells whose
    centre is within ``threshold + slack`` removes the systematic
    false-negative strip at the frontier; the extra over-coverage is absorbed
    by the per-property gate.
    """
    half_diagonal_km = cell_km * math.sqrt(2) / 2
    return (half_diagonal_km / (FREEFLOW_KMH * KMH)).to("minute")


def region_bbox(lat: float, lon: float, region_km: Quantity) -> Rect:
    """Destination-centred square region, ``region_km`` in every direction."""
    km = region_km.to("km").magnitude
    lat_deg = km / KM_PER_DEG_LAT
    lon_deg = km / (KM_PER_DEG_LAT * math.cos(math.radians(lat)))
    return Rect(lat - lat_deg, lat + lat_deg, lon - lon_deg, lon + lon_deg)


def grid_cell_centers(grid: Grid) -> list[GridCell]:
    """All cell centres row-major: ``GridCell(row, col, lat, lon)``.

    Row/col are the grid indices ``union_outline`` traces, so the duration
    list returned by the matrix call aligns with the kept-cell set by index.
    """
    rows = math.ceil((grid.bbox.lat_max - grid.bbox.lat_min) / grid.lat_deg - CELL_COUNT_EPSILON)
    cols = math.ceil((grid.bbox.lon_max - grid.bbox.lon_min) / grid.lon_deg - CELL_COUNT_EPSILON)
    cells: list[GridCell] = []
    for r in range(rows):
        for c in range(cols):
            rect = grid.cell_rect(r, c)
            cells.append(GridCell(r, c, (rect.lat_min + rect.lat_max) / 2.0, (rect.lon_min + rect.lon_max) / 2.0))
    return cells


# ── ORS matrix adapter ───────────────────────────────────────────────


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def build_matrix_requests(
    dest_lon: float, dest_lat: float, centers: list[tuple[float, float]], max_locations: int = MAX_LOCATIONS_PER_REQUEST
) -> list[dict]:
    """Chunk grid centres into matrix request bodies.

    Each body routes every grid point (source) to the destination (index 0),
    i.e. "drive from here to Dad". ``centers`` are ``(lon, lat)`` tuples —
    ORS locations are longitude-first, and a swapped pair silently resolves
    to an ocean point (all-null durations). ``max_locations`` is the ORS
    per-request cap INCLUDING the destination, so each request carries
    ``max_locations - 1`` centres.
    """
    chunk_size = max_locations - 1
    bodies: list[dict] = []
    for start in range(0, len(centers), chunk_size):
        chunk = centers[start : start + chunk_size]
        bodies.append(
            {
                "locations": [[dest_lon, dest_lat], *chunk],
                "sources": list(range(1, len(chunk) + 1)),
                "destinations": [0],
                "metrics": ["duration"],
            }
        )
    return bodies


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def parse_durations(data: dict, count: int) -> list[Quantity | None]:
    """Extract minutes from an ORS matrix response, aligned to the request order.

    ``durations[i][0]`` is the duration (seconds) from the i-th source to the
    destination; ``None`` means the point snapped to no road (e.g. sea) and is
    preserved as unreachable. Returns pint minutes.
    """
    rows = data.get("durations")
    if rows is None or len(rows) != count:
        raise ValueError(
            f"matrix response has {len(rows) if rows is not None else 'no'} duration rows, expected {count}"
        )
    out: list[Quantity | None] = []
    for row in rows:
        if len(row) != 1:
            raise ValueError(f"unexpected duration row {row!r} (expected one destination)")
        out.append(None if row[0] is None else row[0] / SECONDS_PER_MINUTE * MINUTE)
    return out


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def fetch_matrix(body: dict, *, key: str, timeout: float = 120.0, client: Any = None) -> dict:
    """POST one matrix request through the disk cache; retry transient errors.

    Errors are never cached (the cache whitelists 2xx/3xx/404), so a retry is
    a genuine retry — same contract as the transit shed's TfL batch. Only
    transient statuses (429/5xx) and network/timeout failures are retried: a
    400/401/403 (malformed request, bad key) can never succeed on retry and
    fails fast instead of burning a call and stalling ~2 s.

    ``client`` is injectable for tests (DI, not monkeypatching): an async
    context manager exposing ``post(url, json=, headers=)``; default is the
    disk-cached httpx client.
    """

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    headers = {"Content-Type": "application/json", "Authorization": key}
    transient = (429, 500, 502, 503, 504)
    for attempt in (1, 2):
        try:
            cm = client if client is not None else cached_async_client(timeout=timeout)
            async with cm as http:
                resp = await http.post(ORS_MATRIX_URL, json=body, headers=headers)
                if resp.status_code in transient and attempt == 1:
                    await asyncio.sleep(2.0 * attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in transient or attempt == 2:
                raise
            await asyncio.sleep(2.0)
        except (httpx.RequestError, httpx.TimeoutException):
            if attempt == 2:
                raise
            await asyncio.sleep(2.0)
    raise RuntimeError("unreachable")  # pragma: no cover  # unreachable: attempts return or raise, type-checker only


def kept_cells(
    cells: list[GridCell],
    durations_min: Sequence[Quantity | None],
    threshold_min: Quantity,
    slack: Quantity,
) -> set[GridCell]:
    """Cells whose centre drive time fits the threshold (plus boundary slack)."""
    return {
        cell
        for cell, dur in zip(cells, durations_min, strict=True)
        if dur is not None and dur <= threshold_min + slack
    }


# ── payloads ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MatrixBatchConfig:
    """Grid + ORS matrix configuration for one raw-payload build.

    ``fetch`` is injectable for tests: ``async (body, *, key) -> ORS matrix
    response`` — the same signature as the real ``fetch_matrix``; None means
    the real client.
    """

    cell_km: Quantity
    region_km: Quantity
    key: str
    fetch: Callable | None = None


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def build_raw(
    destinations: list[DriveDestination],
    coords: list[GeoPoint],
    config: MatrixBatchConfig,
    generated_at: str,
) -> dict:
    """Geocode-backed matrix batch → the raw isochrone payload.

    Payload values are bare unit-named numbers (the wire format); all
    computation above is Quantity.
    """
    fetch = config.fetch or fetch_matrix
    records: list[dict] = []
    for dest, point in zip(destinations, coords, strict=True):
        bbox = region_bbox(point.lat, point.lon, config.region_km)
        grid = Grid.from_cell_km(bbox, config.cell_km.to("km").magnitude)
        cells = grid_cell_centers(grid)
        centers = [(cell.lon, cell.lat) for cell in cells]  # ORS wants [lon, lat]
        durations: list[Quantity | None] = []
        for body in build_matrix_requests(point.lon, point.lat, centers):
            data = await fetch(body, key=config.key)
            durations.extend(parse_durations(data, len(body["sources"])))
            if _REQUEST_DELAY_S:
                await asyncio.sleep(_REQUEST_DELAY_S)
        records.append(
            {
                "label": dest.label,
                "postcode": dest.postcode,
                "lat": point.lat,
                "lon": point.lon,
                "threshold_min": int(dest.threshold_min.to("minute").magnitude),
                "cell_km": config.cell_km.to("km").magnitude,
                "slack_min": round(slack_minutes(config.cell_km).to("minute").magnitude, MINUTES_DECIMALS),
                "grid": {
                    "lat_min": bbox.lat_min,
                    "lat_max": bbox.lat_max,
                    "lon_min": bbox.lon_min,
                    "lon_max": bbox.lon_max,
                },
                "cells": [
                    {
                        "r": cell.row,
                        "c": cell.col,
                        "lat": cell.lat,
                        "lon": cell.lon,
                        "duration_min": None if dur is None else round(dur.to("minute").magnitude, MINUTES_DECIMALS),
                    }
                    for cell, dur in zip(cells, durations, strict=True)
                ],
            }
        )
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {
        "metadata": {
            "engine_version": ENGINE_VERSION,
            "profile": "driving-car",
            "speed_model": "free-flow",
            "threshold_min": int(min(d.threshold_min for d in destinations).to("minute").magnitude),
            "cell_km": config.cell_km.to("km").magnitude,
            "region_km": config.region_km.to("km").magnitude,
            "destinations": [
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
                {
                    "label": d.label,
                    "postcode": d.postcode,
                    "threshold_min": int(d.threshold_min.to("minute").magnitude),
                }
                for d in destinations
            ],
            "generated_at": generated_at,
            "count": len(records),
        },
        "destinations": records,
    }


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def config_signature(raw: dict) -> dict:
    """The config identity a raw payload was built under.

    If any of these change, previously-fetched durations (routed to the OLD
    destinations/params) must not be reused — same guard the transit shed uses.
    """
    meta = raw["metadata"]
    coords = {
        rec["label"]: (rec["lat"], rec["lon"])
        for rec in raw.get("destinations", [])
        if isinstance(rec, dict) and "label" in rec and "lat" in rec and "lon" in rec
    }
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {
        "engine_version": meta["engine_version"],
        "profile": meta["profile"],
        "threshold_min": meta["threshold_min"],
        "cell_km": meta["cell_km"],
        "region_km": meta["region_km"],
        "destinations": meta["destinations"],
        # a postcode whose geocoding changed (geocoder data update, centroid
        # vs rooftop) must regenerate — never silently reuse a grid centred on
        # the old coordinates
        "coords": coords,
    }


# lucidlint: ignore record-shape component cell sets — homogeneous collections, not field-wise records
def retained_components(
    kept: set[GridCell], min_island_cells: int = MIN_ISLAND_CELLS
) -> list[set[GridCell]]:
    """Components to outline: the main shed ALWAYS, plus satellites of at
    least ``min_island_cells`` cells — the single home of the
    'fringe speckles are noise, the destination's own shed never is' rule
    (drive searches and the intersection both use it)."""
    comps = _components(kept)
    if not comps:
        return []
    return [c for c in comps if c is comps[0] or len(c) >= min_island_cells]


# lucidlint: ignore record-shape component cell sets — homogeneous collections, not field-wise records
def _components(kept: set[GridCell]) -> list[set[GridCell]]:
    """Connected components of the kept-cell set under 4-neighbourhood adjacency.

    Cells touching only at a corner are separate components — the same
    connectivity union_outline traces, so each component outlines cleanly.
    """
    seen: set[GridCell] = set()
    comps: list[set[GridCell]] = []
    for cell in sorted(kept, key=lambda c: (c.row, c.col)):
        if cell in seen:
            continue
        comp: set[GridCell] = {cell}
        stack = [cell]
        seen.add(cell)
        while stack:
            current = stack.pop()
            r, c = current.row, current.col
            for neighbor in (
                GridCell(r - 1, c),
                GridCell(r + 1, c),
                GridCell(r, c - 1),
                GridCell(r, c + 1),
            ):
                if neighbor in kept and neighbor not in seen:
                    seen.add(neighbor)
                    comp.add(neighbor)
                    stack.append(neighbor)
        comps.append(comp)
    # deterministic order: largest (the destination's shed) first, then by position
    return sorted(comps, key=lambda comp: (-len(comp), min(c.row for c in comp), min(c.col for c in comp)))


def _signed_area(poly: list[GeoPoint]) -> float:
    """Shoelace signed area — sign separates outer boundaries from holes.

    union_outline keeps the kept cells on the interior's left, so a component's
    OUTER boundary and an ISLAND boundary share a sign while a HOLE runs the
    opposite way. The outer ring is the largest |area| loop of its component.
    """
    return sum(
        a.lat * b.lon - b.lat * a.lon for a, b in zip(poly, poly[1:] + poly[:1], strict=True)
    ) / 2.0


def outer_loop(cells: set[GridCell], grid: Grid) -> list[GeoPoint] | None:
    """A component's outer boundary loop (holes are absorbed, not traced)."""
    loops = union_outline(cells, grid)
    if not loops:
        return None
    return max(loops, key=lambda loop: abs(_signed_area(loop)))


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def raw_to_searches(
    raw: dict, *, min_beds: int = 2, property_type: str = "houses", generated_at: str, min_island_cells: int = 4
) -> dict:
    """Kept cells → component outlines → Rightmove search records (deterministic).

    Mirrors ``searches.py``'s record shape (id, name, polygon, filters,
    rightmove_url) so the phase-2 scraper consumes drive searches unchanged.
    Vertices are rounded to the polyline encode precision (1e-5) so the JSON
    polygon matches the encoded URL form exactly — the round-trip contract the
    transit rectangles rely on.

    One search per kept component: the main shed and any satellite pocket of
    ``min_island_cells`` cells or more (a 1-2 cell speck on a fast road beyond
    the frontier is fringe noise — a documented small false negative, same
    tolerance the transit plan accepts). Holes inside a component are absorbed:
    the outer polygon already covers them (over-coverage, filtered by the gate)
    and Rightmove cannot draw exclusion holes.
    """
    searches: list[dict] = []
    for dest in raw["destinations"]:
        grid = Grid.from_cell_km(Rect(**dest["grid"]), dest["cell_km"])
        cells = [GridCell(c["r"], c["c"], c["lat"], c["lon"]) for c in dest["cells"]]
        # payload values are bare unit-named numbers (the wire format) —
        # restore units for the Quantity computation
        durations = [None if c["duration_min"] is None else c["duration_min"] * MINUTE for c in dest["cells"]]
        kept = kept_cells(cells, durations, dest["threshold_min"] * MINUTE, dest["slack_min"] * MINUTE)
        slug = re.sub(r"[^a-z0-9]+", "-", dest["label"].lower()).strip("-") or "dest"
        # ids must be unique across destinations: two labels can slug to the
        # same string (e.g. "Dad" and "Dad!"), so the postcode disambiguates
        postcode_slug = re.sub(r"[^a-z0-9]+", "", dest["postcode"].lower()) or "pc"
        for i, comp in enumerate(retained_components(kept, min_island_cells), 1):
            loop = outer_loop(comp, grid)
            if loop is None:
                continue
            poly = [(round(p.lat, POLYLINE_DECIMALS), round(p.lon, POLYLINE_DECIMALS)) for p in loop]
            suffix = "" if i == 1 else f"-{i}"
            searches.append(
                {
                    "id": f"drive-{slug}-{postcode_slug}-{dest['threshold_min']:03d}{suffix}",
                    "name": f"{dest['label']} — {dest['threshold_min']} min drive",
                    "polygon": poly,
                    "filters": {"min_beds": min_beds, "property_type": property_type},
                    "rightmove_url": build_search_url(poly, min_beds=min_beds, property_type=property_type),
                    "destination": {
                        "label": dest["label"], "postcode": dest["postcode"], "lat": dest["lat"], "lon": dest["lon"]
                    },
                    "threshold_min": dest["threshold_min"],
                }
            )
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {
        "metadata": {
            "engine_version": SEARCHES_VERSION,
            "profile": raw["metadata"]["profile"],
            "speed_model": raw["metadata"]["speed_model"],
            "threshold_min": raw["metadata"]["threshold_min"],
            "cell_km": raw["metadata"]["cell_km"],
            "destinations": [d["label"] for d in raw["metadata"]["destinations"]],
            "generated_at": generated_at,
            "count": len(searches),
        },
        "searches": searches,
    }


# ── validation ───────────────────────────────────────────────────────


def point_in_polygon(lat: float, lon: float, poly: list[GeoPoint]) -> bool:
    """Ray-casting point-in-polygon; boundary counts as inside.

    Conservative (boundary-inclusive) matches the toolchain's over-coverage
    bias: a destination sitting exactly on the outline is never declared out.
    The on-segment check is what makes this true — plain ray casting returns
    False for points exactly on an edge or vertex. The check is
    distance-based (not an exact cross-product equality): polygon vertices
    are rounded to 1e-5 degrees, so an exact match would never fire.
    """
    eps = 1e-5  # degrees ≈ 1 m — smaller than the vertices' rounding
    inside = False
    n = len(poly)
    for i in range(n):
        p1 = poly[i]
        p2 = poly[(i + 1) % n]
        if _point_segment_distance(GeoPoint(lat, lon), p1, p2) <= eps:
            return True  # within ~1 m of a segment — on the boundary
        if (p1.lat > lat) != (p2.lat > lat):
            x_cross = p1.lon + (lat - p1.lat) / (p2.lat - p1.lat) * (p2.lon - p1.lon)
            if lon < x_cross:
                inside = not inside
    return inside


def _polygon_is_valid(poly) -> bool:
    """A polygon is a list of 2-element numeric [lat, lon] vertices — the
    shape the geometry and URL checks can safely process."""
    return isinstance(poly, list) and all(
        isinstance(v, (list, tuple))
        and len(v) == 2
        and isinstance(v[0], (int, float))
        and isinstance(v[1], (int, float))
        for v in poly
    )


def _point_segment_distance(point: GeoPoint, a: GeoPoint, b: GeoPoint) -> float:
    """Perpendicular distance from a point to a segment (degrees)."""
    dx, dy = b.lat - a.lat, b.lon - a.lon
    if dx == 0.0 and dy == 0.0:
        return math.hypot(point.lat - a.lat, point.lon - a.lon)
    t = ((point.lat - a.lat) * dx + (point.lon - a.lon) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(point.lat - (a.lat + t * dx), point.lon - (a.lon + t * dy))


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _search_geometry_issues(s: dict, max_vertices: int) -> list[str]:
    """Polygon shape, GB-bbox, and URL round-trip issues for one search."""
    issues: list[str] = []
    poly: Any = s.get("polygon")
    if not _polygon_is_valid(poly):
        issues.append(f"{s.get('id')}: polygon is malformed (expected [lat, lon] pairs)")
        return issues
    if len(poly) < MIN_POLYGON_VERTICES:
        issues.append(f"{s.get('id')}: polygon has {len(poly)} vertices (need ≥ 4)")
    elif poly[0] == poly[-1]:
        issues.append(f"{s.get('id')}: polygon is closed — loops must stay open (the URL builder closes them)")
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
def _destination_label(s: dict) -> str | None:
    """The search's destination label (None when the destination is malformed)."""
    dest = s.get("destination")
    return dest.get("label") if isinstance(dest, dict) else None


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _containment_issues(searches: list[dict]) -> list[str]:
    """Every destination's centre must sit inside one of its own shed's loops."""
    issues: list[str] = []
    by_label: dict[str | None, list[dict]] = {}
    # lucidlint: ignore loop-pipeline group-by accumulation — no comprehension form; groupby would re-sort
    for s in searches:
        by_label.setdefault(_destination_label(s), []).append(s)
    for group in by_label.values():
        valid = [
            o for o in group if _polygon_is_valid(o.get("polygon")) and len(o["polygon"]) >= MIN_CONTAINMENT_VERTICES
        ]
        for s in group:
            d = s.get("destination")
            if not isinstance(d, dict) or not isinstance(d.get("lat"), (int, float)) or not isinstance(
                d.get("lon"), (int, float)
            ):
                issues.append(f"{s.get('id')}: destination is malformed (expected lat/lon)")
                continue
            inside_any = any(
                point_in_polygon(d["lat"], d["lon"], [GeoPoint(lat, lon) for lat, lon in other["polygon"]])
                for other in valid
            )
            if not inside_any:
                issues.append(f"{s.get('id')}: destination centre outside every polygon of its shed")
    return issues


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def validate_payload(payload: dict, *, max_vertices: int = MAX_VERTICES) -> list[str]:
    """Return issues with the drive searches payload; empty means it passes.

    Mirrors the transit validator's remit: geometry sanity, URL round-trip,
    and the no-destination-lost guarantee.
    """
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
    labels_in_searches: set[str] = set()
    for s in searches:
        issues.extend(_search_geometry_issues(s, max_vertices))
        label = _destination_label(s)
        if isinstance(label, str) and label:
            labels_in_searches.add(label)
    issues.extend(
        f"destination {label!r} produced no searches"
        for label in metadata.get("destinations", [])
        if label not in labels_in_searches
    )
    issues.extend(_containment_issues(searches))
    return issues


# ── writing ──────────────────────────────────────────────────────────


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


def _atomic_write(path: Path, content: str) -> None:
    """Write via tmp + os.replace: a concurrent reader (the map build, a
    phone serving the map mid-regeneration) sees the OLD or the NEW file,
    never a partial one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _load_searches_payload(out_dir: Path) -> dict | None:
    """Current searches payload, or None when absent/unreadable (will rewrite)."""
    searches_path = out_dir / SEARCHES_FILENAME
    if not searches_path.exists():
        return None
    try:
        return json.loads(searches_path.read_text())
    except json.JSONDecodeError:
        logger.warning("%s unreadable (corrupt?) — will rewrite", searches_path)
        return None



# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def write_payloads(raw: dict | None, searches: dict, out_dir: str | Path) -> None:
    """Write raw (when regenerated) + searches.json/.txt/.html without churn.

    A regeneration with only a different ``generated_at`` must not dirty the
    committed artifacts — same no-churn contract as ``searches.write_searches``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        _atomic_write(out_dir / RAW_FILENAME, json.dumps(raw, indent=2) + "\n")

    searches_path = out_dir / SEARCHES_FILENAME
    existing = _load_searches_payload(out_dir)
    if existing is not None and _same_payload(existing, searches):
        _write_if_changed(out_dir / TXT_FILENAME, _urls_text(searches))
        _write_if_changed(out_dir / MAP_FILENAME, _map_html(searches))
        return
    _atomic_write(searches_path, json.dumps(searches, indent=2) + "\n")
    _atomic_write(out_dir / TXT_FILENAME, _urls_text(searches))
    _atomic_write(out_dir / MAP_FILENAME, _map_html(searches))


def _write_if_changed(path: Path, content: str) -> None:
    if not path.exists() or path.read_text() != content:
        path.write_text(content)


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _urls_text(payload: dict) -> str:
    return "\n".join(s["rightmove_url"] for s in payload["searches"]) + "\n"


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _map_html(searches: dict) -> str:
    """Leaflet map (Leaflet + CSS from CDN): destination markers + shed
    outlines. The combined map (combined_map.py) is the self-contained one;
    this page is the per-destination quick view."""
    markers_js = []
    outlines_js = []
    seen_labels: set[str] = set()
    for s in searches["searches"]:
        coords = [[lat, lon] for lat, lon in s["polygon"]]
        outlines_js.append(coords)
        d = s["destination"]
        # one marker per destination label, not per search record: a shed
        # that splits into components produces several records at the SAME
        # coordinates — stacked duplicate markers open different URLs
        if d["label"] in seen_labels:
            continue
        seen_labels.add(d["label"])
        # labels are user-controlled (settings) — HTML-escape them (they render
        # via innerHTML) and escape <>& as unicode so no </script> can break
        # out of the script element
        markers_js.append(
            js_safe_json(
                {"label": user_label(d["label"]), "lat": d["lat"], "lon": d["lon"], "url": s["rightmove_url"]}
            )
        )
    html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Drive isochrone coverage</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body{margin:0;height:100%}#map{height:100%}</style></head>
<body><div id="map"></div>
<script>
const outlines = __OUTLINES__;
const markers = __MARKERS__;
const map = L.map('map');
const all = [];
for (const o of outlines) { for (const p of o) { all.push(p); } }
if (all.length) { map.fitBounds(L.latLngBounds(all)); }
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 18}).addTo(map);
for (const o of outlines) {
  L.polygon(o, {color: '#d33', weight: 3, fillOpacity: 0.15}).addTo(map);
}
for (const m of markers) {
  L.marker([m.lat, m.lon]).addTo(map)
    .bindPopup('<b>' + m.label + '</b><br><a href="' + m.url + '" target="_blank">Rightmove search</a>');
}
</script></body></html>
"""
    return html.replace("__OUTLINES__", repr(outlines_js)).replace("__MARKERS__", "[" + ",".join(markers_js) + "]")


# ── CLI ──────────────────────────────────────────────────────────────


async def _geocode(postcode: str) -> GeoPoint:

    attempt = await geocode(postcode)
    point = attempt.value_or_none()
    if point is None:
        # keep the Attempt's structured reason (postcode-not-found vs
        # API-down) so the two-tier dev log can say exactly what failed
        raise RuntimeError(
            f"could not geocode postcode {postcode!r}: {attempt.error or 'unknown reason'}"
        )
    return GeoPoint(point.lat, point.lon)


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _load_raw(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("%s unreadable (corrupt or truncated write?) — will regenerate", path)
        return None


def _fail(user_message: str, dev_detail: str) -> int:
    """Two-tier fail-fast exit (docs/coding-standards.md): a plain-language
    stderr line the user can act on, plus a logger.warning with the exact
    resolution — never one without the other."""
    print(user_message, file=sys.stderr)
    logger.warning(dev_detail)
    return 1


async def _geocode_destinations(geocoder, destinations):
    """Geocode every destination; (coords, None) on success, (None, err) when deferred."""
    try:
        return [await geocoder(d.postcode) for d in destinations], None
    except (RuntimeError, httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException) as e:
        # DEFER geocoding errors (including transient httpx failures): a
        # matching committed raw can still be reused offline (its stored
        # coordinates are part of the signature); the error only matters if
        # regeneration is actually needed
        logger.warning("geocoding failed (%s) — a matching raw payload can still be reused", e)
        return None, e


def _validate_committed(out_dir: Path) -> int:
    """--validate: check the committed searches payload and report."""
    searches_path = out_dir / SEARCHES_FILENAME
    if not searches_path.exists():
        return _fail(
            "No commute map data yet — run 'make commute-drive' first.",
            f"{searches_path} not found for --validate",
        )
    try:
        payload = json.loads(searches_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return _fail(
            "The saved commute map data is unreadable — regenerate it with 'make commute-drive'.",
            f"unreadable {searches_path} for --validate: {e}",
        )
    issues = validate_payload(payload)
    if issues:
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print(f"drive searches OK ({len(payload['searches'])} search(es))")
    return 0


# lucidlint: ignore record-shape (destinations, threshold, region) triple — two-tier exit idiom; NamedTuple is ceremony
def _load_destinations(
    config_path: Path, threshold_override_min: int, region_override_km: float
) -> tuple[list[DriveDestination], Quantity, Quantity] | int:
    """Config → (destinations, shared threshold, region radius); int exit on failure.

    ``threshold_override_min`` (CLI --threshold-min) overrides the config's
    DEFAULT threshold for every destination without an explicit override, so
    the kept cells, metadata, and config signature all agree. The region must
    cover every destination's frontier — the MAX threshold reaches furthest (a
    120-min destination needs a wider grid than a 60-min one, even when the
    payload's default threshold is the min).
    """
    try:
        config_data = json.loads(config_path.read_text())
        destinations = load_config(config_path)
    except (OSError, json.JSONDecodeError, ValueError, AttributeError, TypeError) as e:
        return _fail(
            "The commute destinations settings are missing or unreadable — fix them and try again.",
            f"unreadable drive destinations config {config_path}: {e}",
        )
    if threshold_override_min:
        destinations = apply_default_threshold(destinations, config_data, threshold_override_min * MINUTE)
    threshold_min = min(d.threshold_min for d in destinations)
    if region_override_km:
        region_km = region_override_km * KM
    else:
        region_km = max(d.threshold_min for d in destinations).to("hour") * (REGION_MULTIPLIER * MINUTES_PER_HOUR * KMH)
    return destinations, threshold_min, region_km


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _expected_signature(
    destinations: list[DriveDestination], coords_by_label: dict[str, GeoPoint], *, threshold_min, region_km, cell_km
) -> dict:
    """The config identity the current run expects — a match reuses the raw."""
    return config_signature(
        {
            "metadata": {
                "engine_version": ENGINE_VERSION,
                "profile": "driving-car",
                "speed_model": "free-flow",
                "threshold_min": int(threshold_min.to("minute").magnitude),
                "cell_km": cell_km,
                "region_km": region_km.to("km").magnitude,
                "destinations": [
                    {
                        "label": d.label,
                        "postcode": d.postcode,
                        "threshold_min": int(d.threshold_min.to("minute").magnitude),
                    }
                    for d in destinations
                ],
            },
            "destinations": [
                {"label": d.label, "lat": coords_by_label[d.label].lat, "lon": coords_by_label[d.label].lon}
                for d in destinations
            ],
        }
    )


# lucidlint: ignore record-shape (raw, exit) reuse pair — two-tier exit idiom; a NamedTuple is ceremony
def _reuse_raw(
    args,
    destinations: list[DriveDestination],
    coords: list[GeoPoint] | None,
    *,
    threshold_min,
    region_km,
) -> tuple[dict | None, int | None]:
    """Decide whether the committed raw payload can be reused offline.

    Returns ``(raw, exit_code)``: ``raw`` is the reused payload when the stored
    signature matches; ``exit_code`` is non-None when the stored payload is
    unreadable in a way that must abort the run.
    """
    raw_path = Path(args.out_dir) / RAW_FILENAME
    if not raw_path.exists() or args.force:
        return None, None
    prev = _load_raw(raw_path)
    if prev is None:
        print("The saved drive map data is unreadable — regenerating it.", file=sys.stderr)
        logger.warning("%s unreadable (corrupt or truncated write?) — regenerating", raw_path)
        return None, None
    try:
        prev_signature = config_signature(prev)
        if coords is not None:
            coords_by_label = {d.label: c for d, c in zip(destinations, coords, strict=True)}
        else:
            # geocoding unavailable — compare against the STORED coordinates so
            # an unchanged raw still reuses offline
            coords_by_label = {
                rec["label"]: GeoPoint(rec["lat"], rec["lon"])
                for rec in prev.get("destinations", [])
                if isinstance(rec, dict) and "label" in rec and "lat" in rec and "lon" in rec
            }
        expected = _expected_signature(
            destinations, coords_by_label, threshold_min=threshold_min, region_km=region_km, cell_km=args.cell_km
        )
    except (KeyError, TypeError) as e:
        return None, _fail(
            "The saved commute map data is unreadable — rebuild it from scratch with "
            "'make commute-drive FORCE=1'.",
            f"unreadable raw payload {raw_path}: {e}",
        )
    if prev_signature == expected:
        print(f"reusing {raw_path} ({len(prev['destinations'])} destination(s), offline)")
        return prev, None
    print("The commute settings changed — regenerating the drive map data.", file=sys.stderr)
    logger.warning("config mismatch on %s — regenerating the raw payload", raw_path)
    return None, None


# lucidlint: ignore record-shape (raw, exit) fetch pair — two-tier exit idiom; a NamedTuple is ceremony
async def _fetch_raw(
    destinations: list[DriveDestination],
    coords: list[GeoPoint],
    *,
    cell_km: Quantity,
    region_km: Quantity,
    key: str,
) -> tuple[dict | None, int | None]:
    """Run the ORS matrix batch; auth/transport failures become two-tier exits."""
    if not key:
        return None, _fail(
            user_message=(
                "The commute map can't be generated without the routing API key — add it to your environment "
                "configuration and try again."
            ),
            dev_detail=(
                "HEIGIT_API_KEY unset — set it in .env "
                "(it populates settings.ors_api_key, the OpenRouteService key)"
            ),
        )
    try:
        raw = await build_raw(
            destinations,
            coords,
            MatrixBatchConfig(cell_km=cell_km, region_km=region_km, key=key),
            generated_at=datetime.now(UTC).isoformat(),
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            # auth failures never fix themselves by retrying
            return None, _fail(
                "The commute map service rejected the routing key — check it and try again.",
                f"ORS auth failed ({e.response.status_code}): {e}",
            )
        return None, _fail(
            "The commute map service didn't respond — try again in a minute.",
            f"ORS matrix batch failed: {e}",
        )
    except (
        httpx.RequestError,
        httpx.TimeoutException,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ) as e:
        # a 200 with a malformed body (gateway HTML, truncated response)
        # raises from resp.json()/parse_durations, not from httpx
        return None, _fail(
            "The commute map service didn't respond — try again in a minute.",
            f"ORS matrix batch failed: {e}",
        )
    return raw, None


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _warn_sheds_touching_region_edge(raw: dict) -> None:
    """Kept cells on the region border mean the frontier may exceed --region-km."""
    for record in raw["destinations"]:
        grid = Grid.from_cell_km(Rect(**record["grid"]), record["cell_km"])
        cells = grid_cell_centers(grid)
        kept = kept_cells(
            cells,
            [None if c["duration_min"] is None else c["duration_min"] * MINUTE for c in record["cells"]],
            record["threshold_min"] * MINUTE,
            record["slack_min"] * MINUTE,
        )
        rows = math.ceil((grid.bbox.lat_max - grid.bbox.lat_min) / grid.lat_deg - CELL_COUNT_EPSILON)
        cols = math.ceil((grid.bbox.lon_max - grid.bbox.lon_min) / grid.lon_deg - CELL_COUNT_EPSILON)
        if kept and any(cell.row in (0, rows - 1) or cell.col in (0, cols - 1) for cell in kept):
            logger.warning(
                "%s: kept cells touch the region edge — the 90-min frontier may exceed --region-km; increase it",
                record["label"],
            )
            print(
                f"warning: {record['label']} shed touches the region edge — consider a larger --region-km",
                file=sys.stderr,
            )


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _build_searches_or_fail(
    raw: dict,
    *,
    min_beds: int,
    property_type: str,
    generated_at: str,
    min_island_cells: int,
) -> tuple[dict | None, int | None]:
    """Raw payload → validated searches; a conversion/validation failure is an exit code."""
    try:
        searches = raw_to_searches(
            raw,
            min_beds=min_beds,
            property_type=property_type,
            generated_at=generated_at,
            min_island_cells=min_island_cells,
        )
    except (KeyError, TypeError, ValueError) as e:
        return None, _fail(
            "The saved commute map data is unreadable — rebuild it from scratch with 'make commute-drive FORCE=1'.",
            f"raw→searches conversion failed: {e}",
        )
    issues = validate_payload(searches)
    if issues:
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        print("drive searches FAIL validation — nothing written", file=sys.stderr)
        return None, 1
    return searches, None


def _report(count: int, out_dir: Path) -> None:
    print(f"{count} drive search(es) → {out_dir / SEARCHES_FILENAME}")
    print(f"urls → {out_dir / TXT_FILENAME}")
    print(f"map → {out_dir / MAP_FILENAME}")


async def run(argv: list[str] | None = None, *, geocoder=None, ors_key: str | None = None) -> int:
    """``geocoder`` and ``ors_key`` are injectable for tests (DI): the
    geocoder is ``async (postcode) -> GeoPoint`` (default ``_geocode``);
    ``ors_key`` overrides ``settings.ors_api_key``."""
    parser = argparse.ArgumentParser(description="Build driving isochrone search URLs (one-off ORS matrix batch).")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument(
        "--threshold-min", type=int, default=0, help="override the config's default threshold (minutes)"
    )
    parser.add_argument("--cell-km", type=float, default=DEFAULT_CELL_KM, help="grid cell size in km (default 4)")
    parser.add_argument(
        "--region-km",
        type=float,
        default=0.0,
        help="region radius around each destination (default threshold * 1.7)",
    )
    parser.add_argument("--min-beds", type=int, default=2)
    parser.add_argument(
        "--min-island-cells", type=int, default=4, help="drop shed components below this many cells (default 4)"
    )
    parser.add_argument("--property-type", default="houses")
    parser.add_argument("--force", action="store_true", help="ignore the committed raw payload and re-fetch the matrix")
    parser.add_argument("--validate", action="store_true", help="validate the committed drive_searches.json and exit")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    if args.validate:
        return _validate_committed(out_dir)

    # --threshold-min overrides the config's DEFAULT threshold: applied to
    # every destination without an explicit per-destination override, so the
    # kept cells, metadata, and config signature all agree — a threshold
    # change is a real config change, so the raw payload regenerates (never a
    # silent reuse of cells kept at the old threshold).
    loaded = _load_destinations(Path(args.config), args.threshold_min, args.region_km)
    if isinstance(loaded, int):
        return loaded
    destinations, threshold_min, region_km = loaded
    generated_at = datetime.now(UTC).isoformat()

    if geocoder is None:
        geocoder = _geocode
    coords, geocode_error = await _geocode_destinations(geocoder, destinations)

    raw, reuse_exit = _reuse_raw(
        args,
        destinations,
        coords,
        threshold_min=threshold_min,
        region_km=region_km,
    )
    if reuse_exit is not None:
        return reuse_exit

    if raw is None:
        if coords is None:
            # regeneration is actually needed — the deferred geocoding error
            # is now the real problem
            return _fail(
                "One of the commute destinations can't be found on the map — check its postcode and try again.",
                f"geocoding failed for a drive destination: {geocode_error}",
            )
        api_key = ors_key if ors_key is not None else settings.ors_api_key
        raw, fetch_exit = await _fetch_raw(
            destinations,
            coords,
            cell_km=args.cell_km * KM,
            region_km=region_km,
            key=api_key,
        )
        if fetch_exit is not None:
            return fetch_exit
        assert raw is not None  # fetch_exit None ⇔ the batch succeeded
        _warn_sheds_touching_region_edge(raw)

    searches, searches_exit = _build_searches_or_fail(
        raw,
        min_beds=args.min_beds,
        property_type=args.property_type,
        generated_at=generated_at,
        min_island_cells=args.min_island_cells,
    )
    if searches_exit is not None:
        return searches_exit
    assert searches is not None  # searches_exit None ⇔ the payload validated
    write_payloads(raw, searches, out_dir)
    _report(len(searches["searches"]), out_dir)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(asyncio.run(run()))
