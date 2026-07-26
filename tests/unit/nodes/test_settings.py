"""
Test that the default persons configuration includes all expected persons
with their proper commute destinations.

This is the source of truth for which commutes each property should have.
If this test fails, the persons config has been accidentally changed or
stale test data has leaked into the database, causing properties to miss
commutes for Lorena, George, or Simon's other destinations (Bracknell, Dad).
"""

from unittest.mock import patch

import pytest

from houses.nodes.settings import make_default_persons
from houses.services import Services, _reset_settings_cache


def test_make_default_persons_includes_all_three():
    """Default persons must include Simon, Lorena, and George."""
    persons = make_default_persons()
    names = [p.name for p in persons]
    assert "Simon" in names, "Simon is missing from default persons"
    assert "Lorena" in names, "Lorena is missing from default persons"
    assert "George" in names, "George is missing from default persons"


def test_simon_has_all_commute_destinations():
    """Simon must have Pimlico, Bracknell, and Dad POIs.

    Each destination produces a separate commute entry on every property.
    Missing any means properties will lack that commute column.
    """
    persons = make_default_persons()
    simon = next(p for p in persons if p.name == "Simon")
    labels = [poi.label for poi in simon.places_of_interest]
    assert "Pimlico" in labels, "Simon missing Pimlico commute"
    assert "Bracknell" in labels, "Simon missing Bracknell commute"
    assert "Dad" in labels, "Simon missing Dad commute"
    assert len(simon.places_of_interest) == 3, (
        f"Simon should have 3 POIs, got {len(simon.places_of_interest)}: {labels}"
    )


def test_lorena_has_aldgate_commute():
    """Lorena must commute to Aldgate."""
    persons = make_default_persons()
    lorena = next(p for p in persons if p.name == "Lorena")
    labels = [poi.label for poi in lorena.places_of_interest]
    assert "Aldgate" in labels, "Lorena missing Aldgate commute"
    assert len(lorena.places_of_interest) == 1, f"Lorena should have 1 POI, got {len(lorena.places_of_interest)}"


def test_george_is_child_with_school_pois():
    """George is a child; his POIs are schools, not commute destinations."""
    persons = make_default_persons()
    george = next(p for p in persons if p.name == "George")
    assert george.is_child, "George should be marked as child"
    school_labels = [poi.label for poi in george.places_of_interest]
    assert "Primary School" in school_labels
    assert "Secondary School" in school_labels


def test_services_has_all_three_persons():
    """Services() container should have all 3 persons (Simon, Lorena, George)."""
    svc = Services()
    persons = svc.persons_source._value
    assert persons is not None, "persons_source should have a value"
    names = [p.name for p in persons]
    assert "Simon" in names, f"Simon not in persons_source: {names}"
    assert "Lorena" in names, f"Lorena not in persons_source: {names}"
    assert "George" in names, f"George not in persons_source: {names}"


def test_rejects_stale_test_data_from_db():
    """When the DB has persisted persons with source_label='tests', Services()
    must raise RuntimeError — fail fast so the leak can't go unnoticed."""
    stale_test_data = {
        "status": "succeeded",
        "value": [
            {
                "name": "Simon",
                "has_car": True,
                "is_child": False,
                "bus_walk_penalty_minutes": 30,
                "acceptable_schools": ["mixed"],
                "deposit_equity": None,
                "places_of_interest": [
                    {
                        "label": "Office",
                        "postcode": "SW1V 2QQ",
                        "trips_per_week": 1,
                        "weeks_per_year": 46,
                    }
                ],
            }
        ],
        "source_label": "tests",
    }

    def _mock_latest_node_result(node_id: str):
        if node_id == "persons":
            return stale_test_data
        return None

    with patch("houses.services.latest_node_result", side_effect=_mock_latest_node_result):
        _reset_settings_cache()
        with pytest.raises(RuntimeError, match="Stale test data"):
            Services()
