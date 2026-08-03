"""Tiling — rasterize station catchments onto a grid, row-merge into rectangles.

Cells are kept iff a kept station lies within ``buffer_km`` of the cell's nearest
point (exact point-to-rectangle distance — no centre/diagonal fudge factor).
Row-merged rectangles are disjoint by construction (grid cells are disjoint).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from houses.geo import GeoPoint

KM_PER_DEG_LAT = 111.0


@dataclass(frozen=True)
class Rect:
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


@dataclass(frozen=True)
class Grid:
    """Axis-aligned lat/lon grid with separate degree sizes (square-km cells)."""

    bbox: Rect
    lat_deg: float
    lon_deg: float

    @classmethod
    def from_cell_km(cls, bbox: Rect, cell_size_km: float) -> Grid:
        lat_deg = cell_size_km / KM_PER_DEG_LAT
        mid_lat = (bbox.lat_min + bbox.lat_max) / 2.0
        lon_deg = cell_size_km / (KM_PER_DEG_LAT * math.cos(math.radians(mid_lat)))
        return cls(bbox, lat_deg, lon_deg)

    def cell_rect(self, r: int, c: int) -> Rect:
        """The cell at (row, col), clamped to the bounding box."""
        lat_min = max(self.bbox.lat_min, self.bbox.lat_min + r * self.lat_deg)
        lat_max = min(self.bbox.lat_max, self.bbox.lat_min + (r + 1) * self.lat_deg)
        lon_min = max(self.bbox.lon_min, self.bbox.lon_min + c * self.lon_deg)
        lon_max = min(self.bbox.lon_max, self.bbox.lon_min + (c + 1) * self.lon_deg)
        return Rect(lat_min, lat_max, lon_min, lon_max)


def point_to_rect_distance_km(point: GeoPoint, rect: Rect) -> float:
    """Distance from a point to the nearest point of an axis-aligned rect."""
    lat = min(max(point.lat, rect.lat_min), rect.lat_max)
    lon = min(max(point.lon, rect.lon_min), rect.lon_max)
    return point.distance_km_to(GeoPoint(lat, lon))


def rasterize(stations: list[tuple[float, float]], buffer_km: float, grid: Grid) -> set[tuple[int, int]]:
    """Cells whose nearest point is within ``buffer_km`` of any station.

    Candidate windows are clamped to the grid's valid cell bounds: a station
    near the bbox edge must never generate out-of-bounds cells (they would
    clamp to degenerate zero-area rects and slip through validation as
    line-shaped polygons).
    """
    n_rows = math.ceil((grid.bbox.lat_max - grid.bbox.lat_min) / grid.lat_deg - 1e-9)
    n_cols = math.ceil((grid.bbox.lon_max - grid.bbox.lon_min) / grid.lon_deg - 1e-9)
    kept: set[tuple[int, int]] = set()
    for lat, lon in stations:
        dlat = buffer_km / KM_PER_DEG_LAT
        dlon = buffer_km / (KM_PER_DEG_LAT * math.cos(math.radians(lat)))
        r0 = max(0, math.floor((lat - dlat - grid.bbox.lat_min) / grid.lat_deg))
        r1 = min(n_rows - 1, math.floor((lat + dlat - grid.bbox.lat_min) / grid.lat_deg))
        c0 = max(0, math.floor((lon - dlon - grid.bbox.lon_min) / grid.lon_deg))
        c1 = min(n_cols - 1, math.floor((lon + dlon - grid.bbox.lon_min) / grid.lon_deg))
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                if point_to_rect_distance_km(GeoPoint(lat, lon), grid.cell_rect(r, c)) <= buffer_km:
                    kept.add((r, c))
    return kept


def merge_rows(cells: set[tuple[int, int]], grid: Grid) -> list[Rect]:
    """Merge each row's consecutive kept cells into rectangles."""
    rects: list[Rect] = []
    for row in sorted({r for r, _ in cells}):
        cols = sorted(c for r, c in cells if r == row)
        start = prev = cols[0]
        for c in cols[1:]:
            if c == prev + 1:
                prev = c
            else:
                rects.append(_span(row, start, prev, grid))
                start = prev = c
        rects.append(_span(row, start, prev, grid))
    return rects


def merge_rectangles(rects: list[Rect]) -> list[Rect]:
    """Greedy vertical merge: adjacent rects with identical lon spans combine.

    Keeps coverage identical (the union of cells is unchanged) while cutting the
    rectangle count — rows that share a column band become taller rectangles.
    """
    by_span: dict[tuple[float, float], list[Rect]] = {}
    for r in rects:
        span = (round(r.lon_min, 6), round(r.lon_max, 6))
        by_span.setdefault(span, []).append(r)
    merged: list[Rect] = []
    for (lon_min, lon_max), group in by_span.items():
        group.sort(key=lambda r: r.lat_min)
        current = group[0]
        for r in group[1:]:
            if abs(r.lat_min - current.lat_max) < 1e-9:  # adjacent rows
                current = Rect(current.lat_min, r.lat_max, lon_min, lon_max)
            else:
                merged.append(current)
                current = r
        merged.append(current)
    return sorted(merged, key=lambda r: (r.lat_min, r.lon_min))


def _span(row: int, c0: int, c1: int, grid: Grid) -> Rect:
    lat_min = max(grid.bbox.lat_min, grid.bbox.lat_min + row * grid.lat_deg)
    lat_max = min(grid.bbox.lat_max, grid.bbox.lat_min + (row + 1) * grid.lat_deg)
    lon_min = max(grid.bbox.lon_min, grid.bbox.lon_min + c0 * grid.lon_deg)
    lon_max = min(grid.bbox.lon_max, grid.bbox.lon_min + (c1 + 1) * grid.lon_deg)
    return Rect(lat_min, lat_max, lon_min, lon_max)


def rect_to_polygon(rect: Rect) -> list[tuple[float, float]]:
    """Four-corner polygon: SW, SE, NE, NW (the URL builder closes the loop)."""
    return [
        (rect.lat_min, rect.lon_min),
        (rect.lat_min, rect.lon_max),
        (rect.lat_max, rect.lon_max),
        (rect.lat_max, rect.lon_min),
    ]
