"""Union outlines — trace the outer boundary of the tiled search area.

The search rectangles tile the commute shed on one grid; their union's outer
boundary can be encoded as a single Rightmove polyline, giving one drawn-area
search URL covering a whole connected region. This module traces that boundary
from the kept-cell set with the standard leftmost-turn contour rule, which
resolves pinch points (cells touching only at a corner) correctly.

The shed is NOT one connected blob — station catchments around separate towns
don't touch — so the boundary may be several loops (one per connected
component); each loop is a valid single-polygon search.
"""

from __future__ import annotations

from tools.commute.tile import Grid, rasterize

# unit directions: (dlat, dlon)
N, S, E, W = (1, 0), (-1, 0), (0, 1), (0, -1)
_LEFT = {N: W, W: S, S: E, E: N}  # turn left from a direction


def union_cells(stations: list[tuple[float, float]], buffer_km: float, grid: Grid) -> set[tuple[int, int]]:
    """The kept-cell set (same rasterize the tiling uses) for the union."""
    return rasterize(stations, buffer_km, grid)


def _boundary_segments(
    cells: set[tuple[int, int]], grid: Grid
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Unshared cell sides — each is a boundary segment (lat, lon) -> (lat, lon)."""
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    bbox = grid.bbox
    for r, c in cells:
        # CANONICAL vertex coordinates: always ``base + index * step`` with
        # integer index. ``lat0 + lat_deg`` vs ``(r + 1) * lat_deg`` differ by
        # ULPs for non-decimal steps, so two cells sharing a corner computed
        # differently would produce segments that never connect.
        lat0 = bbox.lat_min + r * grid.lat_deg
        lat1 = bbox.lat_min + (r + 1) * grid.lat_deg
        lon0 = bbox.lon_min + c * grid.lon_deg
        lon1 = bbox.lon_min + (c + 1) * grid.lon_deg
        for (nr, nc), a, b in (
            ((r - 1, c), (lat0, lon0), (lat0, lon1)),  # north side
            ((r + 1, c), (lat1, lon0), (lat1, lon1)),  # south side
            ((r, c - 1), (lat0, lon0), (lat1, lon0)),  # west side
            ((r, c + 1), (lat0, lon1), (lat1, lon1)),  # east side
        ):
            if (nr, nc) not in cells:
                segs.append((a, b))
    return segs


def _direction(a: tuple[float, float], b: tuple[float, float]) -> tuple[int, int]:
    """Axis unit direction from a to b (b is exactly one grid step away)."""
    dlat = 1 if b[0] > a[0] else -1 if b[0] < a[0] else 0
    dlon = 1 if b[1] > a[1] else -1 if b[1] < a[1] else 0
    return (dlat, dlon)


def union_outline(cells: set[tuple[int, int]], grid: Grid) -> list[list[tuple[float, float]]]:
    """Trace all boundary loops (one per connected component), collinear points
    removed. Leftmost-turn rule: at each vertex pick the unused segment that
    turns left first (then straight, then right) — keeps the interior on the
    left and resolves pinch points without cutting through the union."""
    segs = _boundary_segments(cells, grid)
    if not segs:
        return []
    by_start: dict[tuple[float, float], list[tuple[tuple[float, float], int]]] = {}
    for i, (a, b) in enumerate(segs):
        by_start.setdefault(a, []).append((b, i))
        by_start.setdefault(b, []).append((a, i))  # undirected: walk continues through segment ENDS

    used: set[int] = set()
    loops: list[list[tuple[float, float]]] = []

    def pick_next(vertex, incoming):
        """Unused segment at vertex preferring left, straight, right, then any."""
        candidates = [end for end, i in by_start.get(vertex, []) if i not in used]
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        order = [_LEFT[incoming], incoming, _LEFT[_LEFT[incoming]], _LEFT[_LEFT[_LEFT[incoming]]]]
        for d in order:
            for end in candidates:
                if _direction(vertex, end) == d:
                    return end
        return candidates[0]

    def seg_index(vertex, end):
        for e, i in by_start.get(vertex, []):
            if e == end and i not in used:
                return i
        return None

    for start, options in list(by_start.items()):
        first = next(((end, i) for end, i in options if i not in used), None)
        if first is None:
            continue
        end, i = first
        used.add(i)
        loop = [start, end]
        current = end
        incoming = _direction(start, end)
        while True:
            nxt = pick_next(current, incoming)
            if nxt is None:
                break
            si = seg_index(current, nxt)
            if si is None:
                break
            used.add(si)
            if nxt == start:
                break
            incoming = _direction(current, nxt)
            loop.append(nxt)
            current = nxt
        if len(loop) > 2:
            loops.append(loop)

    cleaned_loops = []
    for outline in loops:
        cleaned = []
        n = len(outline)
        for i, (lat, lon) in enumerate(outline):
            plat, plon = outline[(i - 1) % n]
            nlat, nlon = outline[(i + 1) % n]
            same_lat = abs(plat - lat) < 1e-9 and abs(nlat - lat) < 1e-9
            same_lon = abs(plon - lon) < 1e-9 and abs(nlon - lon) < 1e-9
            if not (same_lat or same_lon):
                cleaned.append((lat, lon))
        if cleaned:
            cleaned_loops.append(cleaned)
    return cleaned_loops
