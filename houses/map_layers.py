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
from pathlib import Path

logger = logging.getLogger(__name__)

# Same palettes as tools/commute/combined_map.py — the transit layer always
# takes _COLORS[0]; drive layers use the rest.
_COLORS = ["#e33", "#3a3", "#e80", "#a3a", "#0aa"]
_DRIVE_COLORS = _COLORS[1:]

UNION_PATH = Path("data/commute/union.json")
DRIVE_PATH = Path("data/commute/drive_searches.json")
INTERSECTION_PATH = Path("data/commute/intersection.json")


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None


def _union_layer(union_path: Path):
    """The transit shed layer from the union artifact, or [] when absent."""
    union = _load(union_path)
    layers = []
    if union and union.get("components"):
        layers.append(
            {
                "name": "Train: Pimlico & Aldgate",
                "color": _COLORS[0],
                "polygons": [
                    {"coords": c["outline"], "name": "", "url": ""}
                    for c in union["components"]
                    if c.get("outline")
                ],
            }
        )
    return layers


def _drive_layers(drive_path: Path):
    """One layer per driving destination from the drive searches artifact."""
    drive = _load(drive_path)
    layers = []
    if not drive:
        return layers
    drive_by_label = {}
    for s in drive.get("searches", []):
        label = (s.get("destination") or {}).get("label", "")
        if label:
            drive_by_label.setdefault(label, []).append(s)
    layers.extend(
        {
            "name": f"Drive to {label}",
            "color": _DRIVE_COLORS[(i - 1) % len(_DRIVE_COLORS)],
            "polygons": [
                {"coords": s["polygon"], "name": s.get("name", ""), "url": s.get("rightmove_url", "")}
                for s in searches
            ],
        }
        for i, (label, searches) in enumerate(drive_by_label.items(), 1)
    )
    return layers


def _intersection_layer(intersection_path: Path):
    """The all-commutes intersection layer, or [] when the artifact is absent."""
    intersection = _load(intersection_path)
    layers = []
    if intersection and intersection.get("searches"):
        layers.append(
            {
                "name": "Where we could live",
                "color": "#c90",
                "fillOpacity": 0.25,
                "weight": 4,
                # The headline layer — shown by default; the three
                # isochrone layers start hidden behind the key.
                "visibleByDefault": True,
                "polygons": [
                    {"coords": s["polygon"], "name": s.get("name", ""), "url": s.get("rightmove_url", "")}
                    for s in intersection["searches"]
                ],
            }
        )
    return layers



# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
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
    layers: list[dict] = []
    layers.extend(_union_layer(union_path or UNION_PATH))
    layers.extend(_drive_layers(drive_path or DRIVE_PATH))
    layers.extend(_intersection_layer(intersection_path or INTERSECTION_PATH))
    return layers


