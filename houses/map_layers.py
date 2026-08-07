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


def _load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None


def isochrone_layers() -> list[dict]:
    """The Leaflet layers for the Map page, or [] when no artifacts exist."""
    layers: list[dict] = []

    union = _load(UNION_PATH)
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

    drive = _load(DRIVE_PATH)
    if drive:
        drive_by_label: dict[str, list[dict]] = {}
        for s in drive.get("searches", []):
            label = (s.get("destination") or {}).get("label", "")
            if label:
                drive_by_label.setdefault(label, []).append(s)
        for i, (label, searches) in enumerate(drive_by_label.items(), 1):
            layers.append(
                {
                    "name": f"Drive to {label}",
                    "color": _DRIVE_COLORS[(i - 1) % len(_DRIVE_COLORS)],
                    "polygons": [
                        {"coords": s["polygon"], "name": s.get("name", ""), "url": s.get("rightmove_url", "")}
                        for s in searches
                    ],
                }
            )

    intersection = _load(INTERSECTION_PATH)
    if intersection and intersection.get("searches"):
        layers.append(
            {
                "name": "Where we could live",
                "color": "#c90",
                "fillOpacity": 0.25,
                "weight": 4,
                "polygons": [
                    {"coords": s["polygon"], "name": s.get("name", ""), "url": s.get("rightmove_url", "")}
                    for s in intersection["searches"]
                ],
            }
        )

    return layers
