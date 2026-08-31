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

from dataclasses import dataclass

from houses.geopoint import GeoPoint
from tools.commute.tile import Grid, GridCell

# unit directions: (dlat, dlon)
N, S, E, W = (1, 0), (-1, 0), (0, 1), (0, -1)
# lucidlint: ignore global-state static turn-left lookup table; never mutated
_LEFT = {N: W, W: S, S: E, E: N}  # turn left from a direction
COLLINEAR_EPSILON = 1e-9  # degree tolerance for treating outline points as collinear


@dataclass(frozen=True)
class Segment:
    """One boundary edge of the union: from vertex ``start`` to ``end``."""

    start: GeoPoint
    end: GeoPoint


def _boundary_segments(
    cells: set[GridCell], grid: Grid
) -> list[Segment]:
    """Unshared cell sides — each is a boundary segment between two corners."""
    segs: list[Segment] = []
    bbox = grid.bbox
    for cell in cells:
        r, c = cell.row, cell.col
        # CANONICAL vertex coordinates: always ``base + index * step`` with
        # integer index. ``lat0 + lat_deg`` vs ``(r + 1) * lat_deg`` differ by
        # ULPs for non-decimal steps, so two cells sharing a corner computed
        # differently would produce segments that never connect.
        lat0 = bbox.lat_min + r * grid.lat_deg
        lat1 = bbox.lat_min + (r + 1) * grid.lat_deg
        lon0 = bbox.lon_min + c * grid.lon_deg
        lon1 = bbox.lon_min + (c + 1) * grid.lon_deg
        segs.extend(
            Segment(a, b)
            for (nr, nc), a, b in (
                ((r - 1, c), GeoPoint(lat0, lon0), GeoPoint(lat0, lon1)),  # north side
                ((r + 1, c), GeoPoint(lat1, lon0), GeoPoint(lat1, lon1)),  # south side
                ((r, c - 1), GeoPoint(lat0, lon0), GeoPoint(lat1, lon0)),  # west side
                ((r, c + 1), GeoPoint(lat0, lon1), GeoPoint(lat1, lon1)),  # east side
            )
            if GridCell(nr, nc) not in cells
        )
    return segs


# lucidlint: ignore record-shape (dlat, dlon) direction vector — a lookup-table key for _LEFT, not a data record
def _direction(a: GeoPoint, b: GeoPoint) -> tuple[int, int]:
    """Axis unit direction from a to b (b is exactly one grid step away).

    Returned as a ``(dlat, dlon)`` vector — a lookup-table key for
    ``_LEFT``/``pick_next``, not a record shape.
    """
    dlat = 1 if b.lat > a.lat else -1 if b.lat < a.lat else 0
    dlon = 1 if b.lon > a.lon else -1 if b.lon < a.lon else 0
    return (dlat, dlon)

def _segment_index(segs: list[Segment]) -> dict[GeoPoint, list[tuple[GeoPoint, int]]]:
    """Segment adjacency index: every vertex -> [(neighbour, segment id), ...].

    Undirected: each segment contributes both directions, so the walk can
    continue through segment ENDS as well as starts.
    """
    by_start: dict[GeoPoint, list[tuple[GeoPoint, int]]] = {}
    for i, seg in enumerate(segs):
        by_start.setdefault(seg.start, []).append((seg.end, i))
        by_start.setdefault(seg.end, []).append((seg.start, i))
    return by_start


class _LoopTracer:
    """Walks the segment graph, tracing boundary loops with the leftmost-turn rule.

    Owns the adjacency index and the used-segment set threaded through the
    walk. Leftmost-turn: at each vertex pick the unused segment that turns
    left first (then straight, then right) — keeps the interior on the left
    and resolves pinch points without cutting through the union.
    """

    def __init__(self, by_start: dict[GeoPoint, list[tuple[GeoPoint, int]]]) -> None:
        self._by_start: dict[GeoPoint, list[tuple[GeoPoint, int]]] = by_start
        self._used: set[int] = set()

    # lucidlint: ignore record-shape outline loops — homogeneous point collections, not field-wise records
    def trace_all(self) -> list[list[GeoPoint]]:
        """Trace one loop per still-untraced boundary component."""
        loops: list[list[GeoPoint]] = []
        for start, options in list(self._by_start.items()):
            loop = self._trace_loop(start, options)
            if loop is not None:
                loops.append(loop)
        return loops

    # lucidlint: ignore record-shape (dlat, dlon) direction vector — a lookup-table key, not a data record
    def _pick_next(self, vertex: GeoPoint, incoming: tuple[int, int]) -> GeoPoint | None:
        """Unused segment at vertex preferring left, straight, right, then any."""
        candidates = [end for end, i in self._by_start.get(vertex, []) if i not in self._used]
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

    def _seg_index(self, vertex: GeoPoint, end: GeoPoint) -> int | None:
        for e, i in self._by_start.get(vertex, []):
            if e == end and i not in self._used:
                return i
        return None

    def _trace_loop(self, start: GeoPoint, options) -> list[GeoPoint] | None:
        """Trace one boundary loop from an unused segment start.

        Returns None when every segment at this vertex is already used (no
        loop left to trace).
        """
        first = next(((end, i) for end, i in options if i not in self._used), None)
        if first is None:
            return None
        end, i = first
        self._used.add(i)
        loop = [start, end]
        current = end
        incoming = _direction(start, end)
        while True:
            nxt = self._pick_next(current, incoming)
            if nxt is None:
                break
            # lucidlint: ignore duplicate-block this guard intentionally mirrors the one above — two different fallible
            si = self._seg_index(current, nxt)
            if si is None:
                break
            self._used.add(si)
            if nxt == start:
                break
            incoming = _direction(current, nxt)
            loop.append(nxt)
            current = nxt
        if len(loop) > 2:
            return loop
        return None


# lucidlint: ignore record-shape outline loops — homogeneous point collections, not field-wise records
# lucidlint: ignore record-shape outline loops — homogeneous point collections, not field-wise records
def _remove_collinear(loops: list[list[GeoPoint]]) -> list[list[GeoPoint]]:
    """Drop outline points that lie on a straight run between neighbours."""
    cleaned_loops = []
    for outline in loops:
        cleaned = []
        n = len(outline)
        for i, point in enumerate(outline):
            prev = outline[(i - 1) % n]
            nxt = outline[(i + 1) % n]
            same_lat = abs(prev.lat - point.lat) < COLLINEAR_EPSILON and abs(nxt.lat - point.lat) < COLLINEAR_EPSILON
            same_lon = abs(prev.lon - point.lon) < COLLINEAR_EPSILON and abs(nxt.lon - point.lon) < COLLINEAR_EPSILON
            if not (same_lat or same_lon):
                cleaned.append(point)
        if cleaned:
            cleaned_loops.append(cleaned)
    return cleaned_loops


# lucidlint: ignore record-shape outline loops — homogeneous point collections, not field-wise records
def union_outline(cells: set[GridCell], grid: Grid) -> list[list[GeoPoint]]:
    """Trace all boundary loops (one per connected component), collinear points
    removed. Leftmost-turn rule: at each vertex pick the unused segment that
    turns left first (then straight, then right) — keeps the interior on the
    left and resolves pinch points without cutting through the union."""
    segs = _boundary_segments(cells, grid)
    if not segs:
        return []
    tracer = _LoopTracer(_segment_index(segs))
    return _remove_collinear(tracer.trace_all())
