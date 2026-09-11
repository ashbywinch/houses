"""Isochrone map layers for the website — reads the committed toolchain
artifacts and exposes them as Leaflet layers, matching the shape the
toolchain's own map (tools/commute/combined_map.py) renders.

Layers:
- "Train: …" — the transit shed component outlines from ``union.json``
- "Drive to <label>" — one layer per driving destination from
  ``drive_searches.json``
- "Where we could live" — the all-commutes intersection polygons from
  ``intersection.json``

The word "isochrone" never appears in user-facing copy; it is fine here
(internal code).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Same palettes as tools/commute/combined_map.py — the transit layer always
# takes _COLORS[0]; drive layers use the rest.
_COLORS = ["#e33", "#3a3", "#e80", "#a3a", "#0aa"]
_DRIVE_COLORS = _COLORS[1:]

UNION_PATH = Path("data/commute/union.json")
DRIVE_PATH = Path("data/commute/drive_searches.json")
INTERSECTION_PATH = Path("data/commute/intersection.json")


@dataclass(frozen=True)
class _PolygonJson:
    """A Leaflet polygon entry — the {coords, name, url} map shape."""

    coords: Any
    name: str
    url: str

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the Leaflet polygon shape (coding-standards.md)
        return dict(coords=self.coords, name=self.name, url=self.url)


@dataclass(frozen=True)
class _LayerJson:
    """A Leaflet layer config — the wire shape served to the map page JS.

    The intersection layer also sets fillOpacity/weight/visibleByDefault;
    the transit and drive layers omit them entirely (None fields are
    omitted from to_dict, so the emitted key set matches each site).
    """

    name: str
    color: str
    polygons: list[_PolygonJson]
    fill_opacity: float | None = None
    weight: int | None = None
    visible_by_default: bool | None = None

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the Leaflet layer wire shape (coding-standards.md)
        d: dict[str, object] = dict(name=self.name, color=self.color)
        if self.fill_opacity is not None:
            d["fillOpacity"] = self.fill_opacity
        if self.weight is not None:
            d["weight"] = self.weight
        if self.visible_by_default is not None:
            d["visibleByDefault"] = self.visible_by_default
        d["polygons"] = [p.to_dict() for p in self.polygons]
        return d


@dataclass(frozen=True)
class _UnionComponentJson:
    """One transit-shed component of union.json — the {outline} map shape."""

    outline: Any | None

    @classmethod
    def from_dict(cls, raw: dict) -> _UnionComponentJson:
        return cls(outline=raw.get("outline"))


@dataclass(frozen=True)
class _UnionArtifactJson:
    """The union.json artifact root — the {components} shape."""

    components: list[_UnionComponentJson]

    @classmethod
    def from_dict(cls, raw: dict) -> _UnionArtifactJson:
        return cls(components=[_UnionComponentJson.from_dict(c) for c in raw.get("components") or []])


@dataclass(frozen=True)
class _DriveDestinationJson:
    """The {label} destination block of a drive search."""

    label: str

    @classmethod
    def from_dict(cls, raw: dict) -> _DriveDestinationJson:
        return cls(label=raw.get("label", ""))


@dataclass(frozen=True)
class _DriveSearchJson:
    """One drive-searches entry — the {destination, polygon, name, rightmove_url} shape."""

    destination: _DriveDestinationJson | None
    polygon: Any
    name: str
    rightmove_url: str

    @classmethod
    def from_dict(cls, raw: dict) -> _DriveSearchJson:
        destination = raw.get("destination")
        return cls(
            destination=_DriveDestinationJson.from_dict(destination) if destination else None,
            polygon=raw["polygon"],
            name=raw.get("name", ""),
            rightmove_url=raw.get("rightmove_url", ""),
        )


@dataclass(frozen=True)
class _DriveSearchesJson:
    """The drive_searches.json artifact root — the {searches} shape."""

    searches: list[_DriveSearchJson]

    @classmethod
    def from_dict(cls, raw: dict) -> _DriveSearchesJson:
        return cls(searches=[_DriveSearchJson.from_dict(s) for s in raw.get("searches", [])])


@dataclass(frozen=True)
class _IntersectionSearchJson:
    """One intersection search — the {polygon, name, rightmove_url} shape."""

    polygon: Any
    name: str
    rightmove_url: str

    @classmethod
    def from_dict(cls, raw: dict) -> _IntersectionSearchJson:
        return cls(
            polygon=raw["polygon"],
            name=raw.get("name", ""),
            rightmove_url=raw.get("rightmove_url", ""),
        )


@dataclass(frozen=True)
class _IntersectionArtifactJson:
    """The intersection.json artifact root — the {searches} shape."""

    searches: list[_IntersectionSearchJson]

    @classmethod
    def from_dict(cls, raw: dict) -> _IntersectionArtifactJson:
        return cls(searches=[_IntersectionSearchJson.from_dict(s) for s in raw.get("searches") or []])


# lucidlint: ignore record-shape consumes the committed commute artifacts — toolchain wire payload (coding-standards.md)
def _load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None


def _union_layer(union_path: Path) -> list[_LayerJson]:
    """The transit shed layer from the union artifact, or [] when absent."""
    union = _load(union_path)
    layers = []
    if not union:
        return layers
    artifact = _UnionArtifactJson.from_dict(union)
    if artifact.components:
        layers.append(
            _LayerJson(
                name="Train: Pimlico & Aldgate",
                color=_COLORS[0],
                polygons=[
                    _PolygonJson(coords=component.outline, name="", url="")
                    for component in artifact.components
                    if component.outline
                ],
            )
        )
    return layers


def _drive_layers(drive_path: Path) -> list[_LayerJson]:
    """One layer per driving destination from the drive searches artifact."""
    drive = _load(drive_path)
    layers = []
    if not drive:
        return layers
    artifact = _DriveSearchesJson.from_dict(drive)
    drive_by_label = {}
    for search in artifact.searches:
        label = search.destination.label if search.destination else ""
        if label:
            drive_by_label.setdefault(label, []).append(search)
    layers.extend(
        _LayerJson(
            name=f"Drive to {label}",
            color=_DRIVE_COLORS[(i - 1) % len(_DRIVE_COLORS)],
            polygons=[
                _PolygonJson(coords=search.polygon, name=search.name, url=search.rightmove_url)
                for search in searches
            ],
        )
        for i, (label, searches) in enumerate(drive_by_label.items(), 1)
    )
    return layers


def _intersection_layer(intersection_path: Path) -> list[_LayerJson]:
    """The all-commutes intersection layer, or [] when the artifact is absent."""
    intersection = _load(intersection_path)
    layers = []
    if not intersection:
        return layers
    artifact = _IntersectionArtifactJson.from_dict(intersection)
    if artifact.searches:
        layers.append(
            _LayerJson(
                name="Where we could live",
                color="#c90",
                fill_opacity=0.25,
                weight=4,
                # The headline layer — shown by default; the three
                # isochrone layers start hidden behind the key.
                visible_by_default=True,
                polygons=[
                    _PolygonJson(coords=search.polygon, name=search.name, url=search.rightmove_url)
                    for search in artifact.searches
                ],
            )
        )
    return layers



# lucidlint: ignore record-shape layers list is the module's wire output — assembled from the records' to_dicts
def isochrone_layers(
    *,
    union_path: Path | None = None,
    drive_path: Path | None = None,
    intersection_path: Path | None = None,
) -> list[dict]:
    """The Leaflet layers for the Map page, or [] when no artifacts exist.

    ``union_path``/``drive_path``/``intersection_path`` are test seams
    defaulting to the committed artifact paths, so tests never
    monkeypatch the module constants.
    """
    layers: list[_LayerJson] = []
    layers.extend(_union_layer(union_path or UNION_PATH))
    layers.extend(_drive_layers(drive_path or DRIVE_PATH))
    layers.extend(_intersection_layer(intersection_path or INTERSECTION_PATH))
    return [layer.to_dict() for layer in layers]
