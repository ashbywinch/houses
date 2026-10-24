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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from pint import Quantity
from pydantic import ValidationError

from houses.geopoint import GeoPoint
from tools.commute.drive_isochrone import (
    grid_cell_centers,
    outer_loop,
    point_in_polygon,
    region_bbox,
    retained_components,
    search_geometry_issues,
    validate_committed_payload,
)
from tools.commute.payload_checks import existing_payload, fail, same_payload
from tools.commute.rightmove_url import build_search_url
from tools.commute.searches import IntersectionOptions
from tools.commute.station_shed import ShedPayload, ShedRecord
from tools.commute.tile import Grid, GridCell, Rect, rasterize
from tools.commute.units import Units

logger = logging.getLogger(__name__)

class IntersectionConfig:
    """Constants of the intersection build (review-log: one config class owns
    the engine identity and the grid/transit tunables)."""

    ENGINE_VERSION = "intersection-v1"
    DEFAULT_CELL_KM = 4.0  # same as the drive grids
    TRANSIT_BUFFER_KM = 5.0  # same catchment radius the transit shed rasterizes
    MAX_VERTICES = 500
    POLYLINE_DECIMALS = 5
    MIN_POLYGON_VERTICES = 4
    ROUND_TRIP_EPS = 5e-6  # degree tolerance for URL polygon round-trip comparison
    DEFAULT_CELL_KM_Q = DEFAULT_CELL_KM * Units.KM
    DEFAULT_BUFFER_KM_Q = TRANSIT_BUFFER_KM * Units.KM


DEFAULT_SHED = Path("data/commute/station_shed.json")
DEFAULT_DRIVE_RAW = Path("data/commute/drive_isochrone.json")
DEFAULT_DRIVE_SEARCHES = Path("data/commute/drive_searches.json")
DEFAULT_OUT = Path("data/commute/intersection.json")


# Committed-payload wire shapes (coding-standards.md) — named so signatures say
# which payload flows where; the consumed fields are records, the rest mappings.


type PolygonsByLabel = dict[str, list[list[GeoPoint]]]


class SearchRecord(TypedDict, total=False):
    """One intersection.json search entry — the wire shape."""

    id: str
    name: str
    polygon: list[tuple[float, float]]
    filters: Filters
    rightmove_url: str
    threshold_min: int


class IntersectionPayload(TypedDict, total=False):
    """intersection.json — the wire shape."""

    metadata: IntersectionMetadata
    searches: list[SearchRecord]


class DriveRegionPayload(TypedDict, total=False):
    """The drive artifact's region block — the wire shape."""

    region_km: float
    cell_km: float


class DriveDestinationPayload(TypedDict, total=False):
    """One drive artifact destination — the wire shape."""

    label: str
    postcode: str
    lat: float
    lon: float
    threshold_min: int
    cell_km: float
    slack_min: float
    grid: dict[str, Any]
    cells: list[Any]


class DriveRawPayload(TypedDict, total=False):
    """drive_isochrone.json — the wire shape."""

    metadata: DriveRegionPayload
    destinations: list[DriveDestinationPayload]


class DriveSearchDestinationPayload(TypedDict, total=False):
    """The destination block of a drive-searches entry — the wire shape."""

    label: str
    postcode: str
    lat: float
    lon: float


class DriveSearchPayload(TypedDict, total=False):
    """One drive_searches.json entry — the wire shape."""

    id: str
    name: str
    polygon: list[list[float]]
    rightmove_url: str
    destination: DriveSearchDestinationPayload
    threshold_min: int


class DriveSearchesPayload(TypedDict, total=False):
    """drive_searches.json — the wire shape."""

    metadata: dict[str, Any]
    searches: list[DriveSearchPayload]


class Filters(TypedDict, total=False):
    """Rightmove filter keys as serialized into a search record (wire shape)."""

    min_beds: int
    property_type: str


class IntersectionMetadata(TypedDict, total=False):
    """The metadata block as serialized into intersection.json (wire shape)."""

    engine_version: str
    profile: str
    threshold_min: int
    cell_km: float
    transit_buffer_km: float
    sources: list[str]
    generated_at: str
    count: int


type _ShedPolygon = list[GeoPoint]  # one shed outline loop

@dataclass(frozen=True)
class _SearchFilters:
    """Rightmove filter keys as serialized into a search record (wire shape)."""

    min_beds: int
    property_type: str

    def to_dict(self) -> Filters:
        return Filters(min_beds=self.min_beds, property_type=self.property_type)


@dataclass(frozen=True)
class _IntersectionSearch:
    """One outlined polygon search as serialized into intersection.json (wire shape)."""

    id: str
    name: str
    polygon: list[tuple[float, float]]
    filters: _SearchFilters
    rightmove_url: str
    threshold_min: int

    def to_dict(self) -> SearchRecord:
        return SearchRecord(
            id=self.id,
            name=self.name,
            polygon=self.polygon,
            filters=self.filters.to_dict(),
            rightmove_url=self.rightmove_url,
            threshold_min=self.threshold_min,
        )


@dataclass(frozen=True)
class _IntersectionMetadata:
    """The metadata block as serialized into intersection.json (wire shape)."""

    engine_version: str
    profile: str
    threshold_min: int
    cell_km: float
    transit_buffer_km: float
    sources: list[str]
    generated_at: str
    count: int

    def to_dict(self) -> IntersectionMetadata:
        return IntersectionMetadata(
            engine_version=self.engine_version,
            profile=self.profile,
            threshold_min=self.threshold_min,
            cell_km=self.cell_km,
            transit_buffer_km=self.transit_buffer_km,
            sources=self.sources,
            generated_at=self.generated_at,
            count=self.count,
        )


@dataclass(frozen=True)
class _IntersectionPayload:
    """The intersection payload as serialized into intersection.json (wire shape)."""

    metadata: _IntersectionMetadata
    searches: list[_IntersectionSearch]

    def to_dict(self) -> IntersectionPayload:
        return IntersectionPayload(
            metadata=self.metadata.to_dict(), searches=[s.to_dict() for s in self.searches]
        )


def common_grid(drive_raw: DriveRawPayload, cell_km: Quantity = IntersectionConfig.DEFAULT_CELL_KM_Q) -> Grid:
    """One grid over the overlap of the driving regions.

    The intersection is a subset of every drive region, so cells outside the
    overlap can never qualify — the overlap bbox bounds the search.
    ``cell_km`` is a pint Quantity; payload distances are bare numbers (the
    wire format), restored to units here.
    """
    region_km = drive_raw["metadata"]["region_km"] * Units.KM
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


def transit_cells(
    kept_stations: list[ShedRecord], grid: Grid, buffer_km: Quantity = IntersectionConfig.DEFAULT_BUFFER_KM_Q
) -> set[GridCell]:
    """Cells whose nearest point is within ``buffer_km`` of a kept station.

    Exactly the predicate the transit toolchain rasterizes (``tile.rasterize``)
    — a kept cell means a house in it is within the walk/bus-to-station radius.
    """
    return rasterize([GeoPoint(s.lat, s.lon) for s in kept_stations], buffer_km.to("km").magnitude, grid)


def drive_cells(polygons: list[_ShedPolygon], grid: Grid) -> set[GridCell]:
    """Cells whose centre lies inside any of the destination's shed polygons."""
    if not polygons:
        return set()
    cells = grid_cell_centers(grid)
    return {cell for cell in cells if any(point_in_polygon(cell.lat, cell.lon, p) for p in polygons)}


def _intersection_searches(
    kept: set[GridCell], grid: Grid, thresholds: list[int], min_beds: int, property_type: str
) -> list[_IntersectionSearch]:
    """Kept cells → outlined polygon searches.

    The island filter (main shed always kept) is the drive toolchain's rule —
    one implementation, shared, so tuning it can't diverge.
    """
    searches: list[_IntersectionSearch] = []
    for i, comp in enumerate(retained_components(kept), 1):
        loop = outer_loop(comp, grid)
        if loop is None:
            continue
        poly = [
            (round(p.lat, IntersectionConfig.POLYLINE_DECIMALS), round(p.lon, IntersectionConfig.POLYLINE_DECIMALS))
            for p in loop
        ]
        suffix = "" if i == 1 else f"-{i}"
        searches.append(
            _IntersectionSearch(
                id=f"intersection-{min(thresholds):03d}{suffix}",
                name="All commutes",
                polygon=poly,
                filters=_SearchFilters(min_beds=min_beds, property_type=property_type),
                rightmove_url=build_search_url(poly, min_beds=min_beds, property_type=property_type),
                threshold_min=min(thresholds),
            )
        )
    return searches


class _IntersectionBuild:
    """One all-commutes build: the committed inputs and the steps that turn them
    into the intersection payload.

    Owns the threaded state (the shed, the drive artifact, its searches, the
    options) so the assembly steps are methods on the state they read instead of
    module functions passing ``drive_raw`` through every hop.
    """

    def __init__(
        self,
        *,
        shed: ShedPayload,
        drive_raw: DriveRawPayload,
        drive_searches: DriveSearchesPayload,
        options: IntersectionOptions,
    ) -> None:
        self._shed: ShedPayload = shed
        self._drive_raw: DriveRawPayload = drive_raw
        self._drive_searches: DriveSearchesPayload = drive_searches
        self._options: IntersectionOptions = options

    def payload(self) -> IntersectionPayload:
        """Transit ∩ every drive shed → the intersection payload (deterministic)."""
        grid = common_grid(self._drive_raw, self._options.cell_km)
        kept_stations = [s for s in self._shed.stations if s.kept]
        transit = transit_cells(kept_stations, grid)
        by_label = self._polygons_by_label()
        missing = self._missing_destinations(by_label)
        if missing:
            logger.warning(
                "destination(s) have no shed — the all-commutes intersection is empty: %s", ", ".join(missing)
            )
            return _IntersectionPayload(metadata=self._metadata(count=0), searches=[]).to_dict()
        drive_sets = [drive_cells(polys, grid) for polys in by_label.values()]
        kept = transit
        for ds in drive_sets:
            kept &= ds
        searches = _intersection_searches(
            kept, grid, self._thresholds(), self._options.min_beds, self._options.property_type
        )
        return _IntersectionPayload(metadata=self._metadata(count=len(searches)), searches=searches).to_dict()

    def _metadata(self, *, count: int) -> _IntersectionMetadata:
        """The payload's metadata block for a given search count."""
        return _IntersectionMetadata(
            engine_version=IntersectionConfig.ENGINE_VERSION,
            profile="tfl-transit + driving-car",
            threshold_min=min(self._thresholds()),
            cell_km=self._options.cell_km.to("km").magnitude,
            transit_buffer_km=IntersectionConfig.TRANSIT_BUFFER_KM,
            sources=["station_shed.json", "drive_isochrone.json", "drive_searches.json"],
            generated_at=self._options.generated_at,
            count=count,
        )

    def _thresholds(self) -> list[int]:
        """The per-destination thresholds the drive artifact was built with."""
        return [d["threshold_min"] for d in self._drive_raw["destinations"]]

    def _polygons_by_label(self) -> PolygonsByLabel:
        """Shed polygons per drive destination (a shed may be several loops)."""
        by_label: PolygonsByLabel = {}
        # lucidlint: ignore loop-pipeline group-by accumulation — no comprehension form; groupby would re-sort
        for s in self._drive_searches.get("searches", []):
            by_label.setdefault(s["destination"]["label"], []).append(
                [GeoPoint(lat, lon) for lat, lon in s["polygon"]]
            )
        return by_label

    def _missing_destinations(self, by_label: PolygonsByLabel) -> list[str]:
        """Drive destinations absent from the searches, raising on stale drive data.

        A destination with no shed makes its constraint unsatisfiable: the
        intersection must be EMPTY, never silently dropped ("every commute works"
        would be a lie). The reverse check — drive_searches containing a
        destination ABSENT from the config — means stale committed data: ANDing it
        would silently narrow the intersection; the user must regenerate the drive
        data.
        """
        missing = [d["label"] for d in self._drive_raw["destinations"] if d["label"] not in by_label]
        stale = sorted(set(by_label) - {d["label"] for d in self._drive_raw["destinations"]})
        if stale:
            raise ValueError(
                f"stale drive data: destination(s) {', '.join(stale)} are not in the config — run 'make commute-drive'"
            )
        return missing


def build_payload(
    *,
    shed: ShedPayload,
    drive_raw: DriveRawPayload,
    drive_searches: DriveSearchesPayload,
    options: IntersectionOptions | None = None,
) -> IntersectionPayload:
    """Transit ∩ every drive shed → the intersection payload (deterministic)."""
    return _IntersectionBuild(
        shed=shed,
        drive_raw=drive_raw,
        drive_searches=drive_searches,
        options=options or IntersectionOptions(generated_at=""),
    ).payload()




def validate_payload(payload: Mapping[str, Any], *, max_vertices: int = IntersectionConfig.MAX_VERTICES) -> list[str]:
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
    issues.extend(
        issue
        for s in searches
        for issue in search_geometry_issues(
            s,
            min_polygon_vertices=IntersectionConfig.MIN_POLYGON_VERTICES,
            closed_loop_hint="",
            max_vertices=max_vertices,
            round_trip_eps=IntersectionConfig.ROUND_TRIP_EPS,
        )
    )
    return issues



def _validate_committed(out_path: Path) -> int:
    """--validate: check the committed intersection payload and report."""
    return validate_committed_payload(
        out_path,
        what="all-commutes data",
        make="commute-intersection",
        ok_label="intersection",
        validator=validate_payload,
    )


@dataclass(frozen=True)
class _CommittedInputs:
    """The three committed payloads the map needs."""

    shed: ShedPayload
    raw: DriveRawPayload
    searches: DriveSearchesPayload
def _load_inputs(args) -> _CommittedInputs | int:
    """Read the three committed payloads; int exit code when any is missing/unreadable."""
    missing = [(p, hint) for p, hint in (
        (Path(args.shed), "make commute-shed"),
        (Path(args.drive_raw), "make commute-drive"),
        (Path(args.drive_searches), "make commute-drive"),
    ) if not Path(p).exists()]
    if missing:
        hints = list(dict.fromkeys(h for _, h in missing))
        return fail(
            f"Commute data is missing — run {' and '.join(hints)} first.",
            f"intersection inputs not found: {', '.join(str(p) for p, _ in missing)}",
        )
    try:
        shed = ShedPayload(**json.loads(Path(args.shed).read_text()))
        drive_raw = json.loads(Path(args.drive_raw).read_text())
        drive_searches = json.loads(Path(args.drive_searches).read_text())
    except (json.JSONDecodeError, OSError, ValidationError) as e:
        return fail(
            "Commute data is unreadable — regenerate it with 'make commute-drive'.",
            f"unreadable intersection input: {e}",
        )
    return _CommittedInputs(shed=shed, raw=drive_raw, searches=drive_searches)


def _write_if_changed(out_path: Path, payload: IntersectionPayload) -> None:
    """Write the payload atomically, skipping a byte-identical committed copy."""
    existing = existing_payload(out_path)
    if existing is None or not same_payload(existing, dict(payload)):
        # dict(payload): the TypedDict is a dict at runtime — the wire
        # comparison helper types its args as plain dicts (pyrefly: TypedDict
        # → dict needs an explicit projection).
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
    parser.add_argument("--cell-km", type=float, default=IntersectionConfig.DEFAULT_CELL_KM)
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
                cell_km=args.cell_km * Units.KM,
                min_beds=args.min_beds,
                property_type=args.property_type,
            ),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return fail(
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
