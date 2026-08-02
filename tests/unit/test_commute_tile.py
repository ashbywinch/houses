"""Tiling — rasterize the station catchment onto a grid and row-merge into rectangles."""

from __future__ import annotations

import math

import pytest

from houses.geo import GeoPoint
from tools.commute.tile import Grid, Rect, merge_rows, point_to_rect_distance_km, rasterize, rect_to_polygon

# Grid: 0.1 deg × 0.1 deg cells over lat 51.0-51.4, lon -1.0 to -0.6 (4×4 cells).
# At lat ~51, 0.1 deg lat = 11.1 km; 0.1 deg lon = ~7.0 km.
BBOX = Rect(lat_min=51.0, lat_max=51.4, lon_min=-1.0, lon_max=-0.6)
GRID = Grid(bbox=BBOX, lat_deg=0.1, lon_deg=0.1)
BUFFER_KM = 5.0


# ── point-to-rectangle distance ──────────────────────────────────────


def test_point_inside_rect_distance_zero():
    rect = Rect(lat_min=51.0, lat_max=51.1, lon_min=-1.0, lon_max=-0.9)
    assert point_to_rect_distance_km(GeoPoint(51.05, -0.95), rect) == pytest.approx(0.0)


def test_point_north_of_rect():
    rect = Rect(lat_min=51.0, lat_max=51.1, lon_min=-1.0, lon_max=-0.9)
    d = point_to_rect_distance_km(GeoPoint(51.15, -0.95), rect)
    # 0.05 deg lat ≈ 5.55 km
    assert d == pytest.approx(5.55, abs=0.05)


def test_point_east_of_rect():
    rect = Rect(lat_min=51.0, lat_max=51.1, lon_min=-1.0, lon_max=-0.9)
    d = point_to_rect_distance_km(GeoPoint(51.05, -0.85), rect)
    # 0.05 deg lon ≈ 3.49 km
    assert d == pytest.approx(3.49, abs=0.05)


def test_point_at_corner_diagonal():
    rect = Rect(lat_min=51.0, lat_max=51.1, lon_min=-1.0, lon_max=-0.9)
    d = point_to_rect_distance_km(GeoPoint(51.15, -0.85), rect)
    assert d == pytest.approx(math.hypot(5.55, 3.49), abs=0.1)


# ── rasterize ────────────────────────────────────────────────────────


def test_rasterize_keeps_cells_within_buffer():
    cells = rasterize([(51.05, -0.95)], BUFFER_KM, GRID)
    # Station inside cell (0,0); east/west neighbours have nearest-point
    # distance ~3.5 km (within buffer); north/south are ~5.5 km (out).
    assert (0, 0) in cells
    assert (0, 1) in cells
    assert (0, -1) in cells
    assert (1, 0) not in cells
    assert (-1, 0) not in cells


def test_rasterize_far_station_keeps_nothing():
    cells = rasterize([(51.05, -0.5)], BUFFER_KM, GRID)
    # Station 0.3 deg east of the grid — beyond buffer + cell reach.
    assert cells == set()


def test_rasterize_deterministic():
    stations = [(51.05, -0.95), (51.12, -0.88), (51.03, -0.99)]
    assert rasterize(stations, BUFFER_KM, GRID) == rasterize(stations, BUFFER_KM, GRID)


def test_every_kept_cell_is_within_buffer():
    stations = [(51.05, -0.95), (51.12, -0.88), (51.03, -0.99)]
    cells = rasterize(stations, BUFFER_KM, GRID)
    for r, c in cells:
        rect = GRID.cell_rect(r, c)
        assert any(point_to_rect_distance_km(GeoPoint(lat, lon), rect) <= BUFFER_KM + 1e-6 for lat, lon in stations)


# ── merge_rows ───────────────────────────────────────────────────────


def test_merge_rows_merges_consecutive_spans():
    cells = {(0, 0), (0, 1), (0, 2), (1, 0), (2, 2), (2, 3)}
    rects = merge_rows(cells, GRID)
    assert len(rects) == 3
    # Row 0 merged into one span covering cols 0-2 (lon -1.0..-0.7).
    row0 = next(r for r in rects if r.lat_min == pytest.approx(51.0))
    assert row0.lon_min == pytest.approx(-1.0)
    assert row0.lon_max == pytest.approx(-0.7)
    assert row0.lat_max == pytest.approx(51.1)
    # Row 2 has a two-cell span (lon -0.8..-0.6).
    row2 = next(r for r in rects if r.lat_min == pytest.approx(51.2))
    assert row2.lon_min == pytest.approx(-0.8)
    assert row2.lon_max == pytest.approx(-0.6)


def test_merge_rows_disjoint():
    cells = {(0, 0), (0, 1), (0, 3), (1, 0), (2, 2)}
    rects = merge_rows(cells, GRID)
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            overlap = not (
                a.lat_max <= b.lat_min
                or b.lat_max <= a.lat_min
                or a.lon_max <= b.lon_min
                or b.lon_max <= a.lon_min
            )
            assert not overlap


def test_merge_rows_deterministic():
    cells = {(0, 0), (0, 1), (0, 3), (1, 0), (2, 2)}
    assert merge_rows(cells, GRID) == merge_rows(cells, GRID)


# ── polygon ──────────────────────────────────────────────────────────


def test_rect_to_polygon_four_corners():
    rect = Rect(lat_min=51.0, lat_max=51.1, lon_min=-1.0, lon_max=-0.9)
    assert rect_to_polygon(rect) == [(51.0, -1.0), (51.0, -0.9), (51.1, -0.9), (51.1, -1.0)]
