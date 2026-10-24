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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field

from serialization import SerializableAsDict, omit_when_none

logger = logging.getLogger(__name__)

class _MapColors:
    """Same palettes as tools/commute/combined_map.py — the transit layer
    always takes BASE[0]; drive layers use the rest."""

    BASE = ["#e33", "#3a3", "#e80", "#a3a", "#0aa"]
    DRIVE = BASE[1:]

UNION_PATH = Path("data/commute/union.json")
DRIVE_PATH = Path("data/commute/drive_searches.json")
INTERSECTION_PATH = Path("data/commute/intersection.json")


class _Polygon(SerializableAsDict):
    """A Leaflet polygon entry — the {coords, name, url} map shape."""

    coords: Any
    name: str
    url: str



class _Layer(SerializableAsDict):
    """A Leaflet layer config — the wire shape served to the map page JS.

    The intersection layer also sets fillOpacity/weight/visibleByDefault;
    the transit and drive layers omit them entirely, so each styling key
    is excluded while it is unset and the emitted key set matches the site.
    """

    name: str
    color: str
    polygons: list[_Polygon]
    fill_opacity: float | None = Field(default=None, alias="fillOpacity", exclude_if=omit_when_none)
    weight: int | None = Field(default=None, exclude_if=omit_when_none)
    visible_by_default: bool | None = Field(default=None, alias="visibleByDefault", exclude_if=omit_when_none)


@dataclass(frozen=True)
class _UnionComponent:
    """One transit-shed component of union.json — the {outline} map shape."""

    outline: Any | None

    @classmethod
    def from_dict(cls, raw: dict) -> _UnionComponent:
        return cls(outline=raw.get("outline"))


@dataclass(frozen=True)
class _UnionArtifact:
    """The union.json artifact root — the {components} shape."""

    components: list[_UnionComponent]

    @classmethod
    def from_dict(cls, raw: dict) -> _UnionArtifact:
        return cls(components=[_UnionComponent.from_dict(c) for c in raw.get("components") or []])


@dataclass(frozen=True)
class _DriveDestination:
    """The {label} destination block of a drive search."""

    label: str

    @classmethod
    def from_dict(cls, raw: dict) -> _DriveDestination:
        return cls(label=raw.get("label", ""))


@dataclass(frozen=True)
class _DriveSearch:
    """One drive-searches entry — the {destination, polygon, name, rightmove_url} shape."""

    destination: _DriveDestination | None
    polygon: Any
    name: str
    rightmove_url: str

    @classmethod
    def from_dict(cls, raw: dict) -> _DriveSearch:
        destination = raw.get("destination")
        return cls(
            destination=_DriveDestination.from_dict(destination) if destination else None,
            polygon=raw["polygon"],
            name=raw.get("name", ""),
            rightmove_url=raw.get("rightmove_url", ""),
        )


@dataclass(frozen=True)
class _DriveSearches:
    """The drive_searches.json artifact root — the {searches} shape."""

    searches: list[_DriveSearch]

    @classmethod
    def from_dict(cls, raw: dict) -> _DriveSearches:
        return cls(searches=[_DriveSearch.from_dict(s) for s in raw.get("searches", [])])


@dataclass(frozen=True)
class _IntersectionSearch:
    """One intersection search — the {polygon, name, rightmove_url} shape."""

    polygon: Any
    name: str
    rightmove_url: str

    @classmethod
    def from_dict(cls, raw: dict) -> _IntersectionSearch:
        return cls(
            polygon=raw["polygon"],
            name=raw.get("name", ""),
            rightmove_url=raw.get("rightmove_url", ""),
        )


@dataclass(frozen=True)
class _IntersectionArtifact:
    """The intersection.json artifact root — the {searches} shape."""

    searches: list[_IntersectionSearch]

    @classmethod
    def from_dict(cls, raw: dict) -> _IntersectionArtifact:
        return cls(searches=[_IntersectionSearch.from_dict(s) for s in raw.get("searches") or []])


def _load_artifact(path: Path, from_dict: Callable[[Any], Any]) -> Any | None:
    """Load and parse one committed artifact, or None when it is absent,
    unreadable, or not a JSON object — the artifact loaders' shared step."""
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None
    if not isinstance(raw, Mapping):
        logger.warning("%s is not a JSON object — treating it as absent", path)
        return None
    return from_dict(raw)


def _artifact_layer(
    path: Path,
    from_dict: Callable[[Any], Any],
    items: Callable[[Any], Sequence[Any]],
    build: Callable[[list[_Polygon]], _Layer],
) -> list[_Layer]:
    """Load one committed artifact and build its single layer — [] when the
    artifact is absent or carries no items (the two artifact layers' shared
    step: load, parse, select, build)."""
    artifact = _load_artifact(path, from_dict)
    if artifact is None:
        return []
    selection = items(artifact)
    if not selection:
        return []
    polygons = [poly for item in selection if (poly := _item_polygon(item)) is not None]
    return [build(polygons)]


def _union_layer(union_path: Path) -> list[_Layer]:
    """The transit shed layer from the union artifact, or [] when absent."""
    return _artifact_layer(
        union_path,
        _UnionArtifact.from_dict,
        lambda artifact: artifact.components,
        lambda polygons: _Layer(name="Train: Pimlico & Aldgate", color=_MapColors.BASE[0], polygons=polygons),
    )


def _drive_layers(drive_path: Path) -> list[_Layer]:
    """One layer per driving destination from the drive searches artifact."""
    artifact = _load_artifact(drive_path, _DriveSearches.from_dict)
    layers: list[_Layer] = []
    if artifact is None:
        return layers
    drive_by_label = {}
    for search in artifact.searches:
        label = search.destination.label if search.destination else ""
        if label:
            drive_by_label.setdefault(label, []).append(search)
    layers.extend(
        _Layer(
            name=f"Drive to {label}",
            color=_MapColors.DRIVE[(i - 1) % len(_MapColors.DRIVE)],
            polygons=[
                _Polygon(coords=search.polygon, name=search.name, url=search.rightmove_url)
                for search in searches
            ],
        )
        for i, (label, searches) in enumerate(drive_by_label.items(), 1)
    )
    return layers


def _item_polygon(item: Any) -> _Polygon | None:
    """The polygon of one artifact item, or None when it has no outline —
    the two artifact shapes differ only in their field names."""
    if hasattr(item, "outline"):
        return _Polygon(coords=item.outline, name="", url="") if item.outline else None
    return _Polygon(coords=item.polygon, name=item.name, url=item.rightmove_url)


def _intersection_layer(intersection_path: Path) -> list[_Layer]:
    """The all-commutes intersection layer, or [] when the artifact is absent."""
    return _artifact_layer(
        intersection_path,
        _IntersectionArtifact.from_dict,
        lambda artifact: artifact.searches,
        lambda polygons: _Layer(
            name="Where we could live",
            color="#c90",
            fill_opacity=0.25,
            weight=4,
            # The headline layer — shown by default; the three
            # isochrone layers start hidden behind the key.
            visible_by_default=True,
            polygons=polygons,
        ),
    )



def isochrone_layers(
    *,
    union_path: Path | None = None,
    drive_path: Path | None = None,
    intersection_path: Path | None = None,
) -> list[_Layer]:
    """The Leaflet layers for the Map page, or [] when no artifacts exist.

    ``union_path``/``drive_path``/``intersection_path`` are test seams
    defaulting to the committed artifact paths, so tests never
    monkeypatch the module constants.
    """
    layers: list[_Layer] = []
    layers.extend(_union_layer(union_path or UNION_PATH))
    layers.extend(_drive_layers(drive_path or DRIVE_PATH))
    layers.extend(_intersection_layer(intersection_path or INTERSECTION_PATH))
    return layers
