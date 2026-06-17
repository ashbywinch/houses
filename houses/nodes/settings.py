"""Settings SourceNodes for the reactive DAG.

Settings are stored as SourceNode values so changes propagate
reactively through the graph. Default values mirror the existing
houses/config.py Settings class.
"""

from __future__ import annotations

from typing import Any

from dag.attempt import Provenance
from dag.source_node import SourceNode
from houses.config import settings
from houses.model.domain import Person, PlaceOfInterest


def make_default_settings() -> dict[str, Any]:
    """Build the default settings dict from config and domain defaults."""
    return {
        "persons": [
            Person(
                name="Simon",
                has_car=True,
                deposit_equity=None,
                places_of_interest=(
                    PlaceOfInterest(label="Office", postcode=settings.simon_postcode),
                    PlaceOfInterest(label="Bracknell", postcode=settings.bracknell_postcode),
                    PlaceOfInterest(label="Dad", postcode="OX7 5GZ"),
                ),
            ),
            Person(
                name="Lorena",
                has_car=False,
                deposit_equity=None,
                places_of_interest=(
                    PlaceOfInterest(label="Office", postcode=settings.lorena_postcode),
                ),
            ),
        ],
        "commute_thresholds": {
            "Simon": {"good_max_minutes": 30, "fine_max_minutes": 45},
            "Lorena": {"good_max_minutes": 40, "fine_max_minutes": 60},
        },
        "bus_walk_penalty_minutes": settings.bus_walk_penalty_minutes,
    }


def make_settings_node() -> SourceNode[dict[str, Any]]:
    """Create a new settings SourceNode populated with defaults."""
    node = SourceNode[dict[str, Any]]("settings", dict[str, Any])
    node.push(make_default_settings(), Provenance("config"))
    return node


settings_node = make_settings_node()
