"""Union outlines — the tiled search area's outer boundaries (one per component)."""

from __future__ import annotations

from tools.commute.tile import Grid, Rect
from tools.commute.union import union_outline

GRID = Grid(bbox=Rect(lat_min=51.0, lat_max=51.4, lon_min=-1.0, lon_max=-0.6), lat_deg=0.1, lon_deg=0.1)


def test_single_cell_one_loop():
    loops = union_outline({(0, 0)}, GRID)
    assert len(loops) == 1
    assert loops[0] == [(51.0, -1.0), (51.0, -0.9), (51.1, -0.9), (51.1, -1.0)]


def test_two_cells_one_rectangle():
    loops = union_outline({(0, 0), (0, 1)}, GRID)
    assert len(loops) == 1
    assert len(loops[0]) == 4  # the 1x2 strip's corners


def test_l_shape_outline():
    loops = union_outline({(0, 0), (0, 1), (1, 0)}, GRID)
    assert len(loops) == 1
    assert set(loops[0]) == {
        (51.0, -0.8),
        (51.1, -0.8),
        (51.1, -0.9),
        (51.2, -0.9),
        (51.2, -1.0),
        (51.0, -1.0),
    }


def test_pinch_point_diagonal_cells():
    # Two cells touching only at a corner: the union is NOT a simple polygon —
    # it decomposes into two loops (one per cell), sharing the pinch vertex.
    # Two separate polygons is the correct encoding for Rightmove.
    loops = union_outline({(0, 0), (1, 1)}, GRID)
    assert len(loops) == 2
    assert all(len(loop) == 4 for loop in loops)
    assert (51.1, -0.9) in loops[0] and (51.1, -0.9) in loops[1]  # the shared pinch vertex


def test_disconnected_components_two_loops():
    loops = union_outline({(0, 0), (2, 2)}, GRID)
    assert len(loops) == 2


def test_adjacent_cells_connect_with_irrational_grid_step():
    # Regression: lon_deg here is non-decimal (8 km / cos(lat)), so
    # ``lon0 + lon_deg`` vs ``(c + 1) * lon_deg`` differed by ULPs and the two
    # cells' shared corner never connected — the walker fragmented the boundary.
    real_grid = Grid.from_cell_km(
        Rect(lat_min=50.1, lat_max=53.6, lon_min=-4.0, lon_max=2.0), 8.0
    )
    loops = union_outline({(10, 10), (10, 11)}, real_grid)
    assert len(loops) == 1
    assert len(loops[0]) == 4  # one closed rectangle, not fragments


def test_outline_deterministic():
    cells = {(0, 0), (0, 1), (1, 0), (1, 2)}
    assert union_outline(cells, GRID) == union_outline(cells, GRID)


def test_empty_cells_empty_outline():
    assert union_outline(set(), GRID) == []
