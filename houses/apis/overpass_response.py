"""The Overpass query result: the parsed element rows."""

from __future__ import annotations

from dataclasses import dataclass

from houses.apis.overpass_element import OverpassElement


@dataclass(frozen=True)
class OverpassResponse:
    elements: list[OverpassElement]