"""Tiling — rasterize station catchments onto a grid, row-merge into rectangles.

Cells are kept iff a kept station lies within ``buffer_km`` of the cell's nearest
point (exact point-to-rectangle distance — no centre/diagonal fudge factor).
Row-merged rectangles are disjoint by construction (grid cells are disjoint).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from houses.geopoint import GeoPoint

KM_PER_DEG_LAT = 111.0
CELL_COUNT_EPSILON = 1e-9  # float-error guard before ceil — exact multiples must not add a phantom row
ADJACENCY_EPSILON = 1e-9  # rows within this degree gap count as adjacent for merging
RECT_SPAN_DECIMALS = 6  # lon-span rounding for the vertical-merge grouping key


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
        """The cell at (row, col): a single-column span, clamped to the bounding box."""
        return _span(r, c, c, self)


@dataclass(frozen=True, eq=False)
class GridCell:
    """A grid cell: the (row, col) index plus its centre (lat, lon).

    Identity is the (row, col) index — two cells at the same index are the
    same cell regardless of how the centre was computed, so set membership
    across the toolchain's different centre producers is ULP-safe.
    """

    row: int
    col: int
    lat: float = 0.0
    lon: float = 0.0

    def __eq__(self, other: object) -> bool:
        return isinstance(other, GridCell) and self.row == other.row and self.col == other.col

    def __hash__(self) -> int:
        return hash((self.row, self.col))


def point_to_rect_distance_km(point: GeoPoint, rect: Rect) -> float:
    """Distance from a point to the nearest point of an axis-aligned rect."""
    lat = min(max(point.lat, rect.lat_min), rect.lat_max)
    lon = min(max(point.lon, rect.lon_min), rect.lon_max)
    return point.distance_km_to(GeoPoint(lat, lon))


def rasterize(stations: list[GeoPoint], buffer_km: float, grid: Grid) -> set[GridCell]:
    """Cells whose nearest point is within ``buffer_km`` of any station.

    Candidate windows are clamped to the grid's valid cell bounds: a station
    near the bbox edge must never generate out-of-bounds cells (they would
    clamp to degenerate zero-area rects and slip through validation as
    line-shaped polygons).
    """
    n_rows = math.ceil((grid.bbox.lat_max - grid.bbox.lat_min) / grid.lat_deg - CELL_COUNT_EPSILON)
    n_cols = math.ceil((grid.bbox.lon_max - grid.bbox.lon_min) / grid.lon_deg - CELL_COUNT_EPSILON)
    kept: set[GridCell] = set()
    for point in stations:
        dlat = buffer_km / KM_PER_DEG_LAT
        dlon = buffer_km / (KM_PER_DEG_LAT * math.cos(math.radians(point.lat)))
        r0 = max(0, math.floor((point.lat - dlat - grid.bbox.lat_min) / grid.lat_deg))
        r1 = min(n_rows - 1, math.floor((point.lat + dlat - grid.bbox.lat_min) / grid.lat_deg))
        c0 = max(0, math.floor((point.lon - dlon - grid.bbox.lon_min) / grid.lon_deg))
        c1 = min(n_cols - 1, math.floor((point.lon + dlon - grid.bbox.lon_min) / grid.lon_deg))
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                rect = grid.cell_rect(r, c)
                if point_to_rect_distance_km(point, rect) <= buffer_km:
                    kept.add(GridCell(r, c, (rect.lat_min + rect.lat_max) / 2.0, (rect.lon_min + rect.lon_max) / 2.0))
    return kept


def merge_rows(cells: set[GridCell], grid: Grid) -> list[Rect]:
    """Merge each row's consecutive kept cells into rectangles."""
    rects: list[Rect] = []
    for row in sorted({cell.row for cell in cells}):
        cols = sorted(cell.col for cell in cells if cell.row == row)
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
        span = (round(r.lon_min, RECT_SPAN_DECIMALS), round(r.lon_max, RECT_SPAN_DECIMALS))
        by_span.setdefault(span, []).append(r)
    merged: list[Rect] = []
    for (lon_min, lon_max), group in by_span.items():
        group.sort(key=lambda r: r.lat_min)
        current = group[0]
        for r in group[1:]:
            if abs(r.lat_min - current.lat_max) < ADJACENCY_EPSILON:  # adjacent rows
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


def rect_to_polygon(rect: Rect) -> list[GeoPoint]:
    """Four-corner polygon: SW, SE, NE, NW (the URL builder closes the loop)."""
    return [
        GeoPoint(rect.lat_min, rect.lon_min),
        GeoPoint(rect.lat_min, rect.lon_max),
        GeoPoint(rect.lat_max, rect.lon_max),
        GeoPoint(rect.lat_max, rect.lon_min),
    ]
