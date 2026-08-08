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


def test_default_persons_carry_explicit_modes_and_guardians():
    """Defaults set acceptable_modes and editable_by explicitly — the
    label-migration rule is only for legacy persisted data."""
    persons = make_default_persons()
    simon = next(p for p in persons if p.name == "Simon")
    assert {poi.label: poi.acceptable_modes for poi in simon.places_of_interest} == {
        "Pimlico": ("transit",),
        "Bracknell": ("car",),
        "Dad": ("car",),
    }
    lorena = next(p for p in persons if p.name == "Lorena")
    assert {poi.label: poi.acceptable_modes for poi in lorena.places_of_interest} == {"Aldgate": ("transit",)}
    george = next(p for p in persons if p.name == "George")
    assert set(george.editable_by) == {"Simon", "Lorena", "Ashby"}
    assert all(poi.acceptable_modes == ("walk",) for poi in george.places_of_interest)


def test_effective_acceptable_modes_migration_rule():
    """Unset (legacy) modes migrate by rule: offices → train, out-of-London
    trips → car, schools → walk; anything else keeps the old all-modes
    behaviour (no inference for unknown labels)."""
    from houses.model.domain import PlaceOfInterest, effective_acceptable_modes

    assert effective_acceptable_modes(PlaceOfInterest("Pimlico", "1 Drummond Gate, Pimlico, London SW1V 2QQ")) == (
        "transit",
    )
    assert effective_acceptable_modes(
        PlaceOfInterest("Aldgate", "Eastgate House, 40 Dukes Place, London EC3A 7LP")
    ) == ("transit",)
    assert effective_acceptable_modes(
        PlaceOfInterest("Bracknell", "Waite House, Doncastle Road, Bracknell RG12 8YA")
    ) == ("car",)
    assert effective_acceptable_modes(PlaceOfInterest("Dad", "Flat 37, Watson Place, Trinity Road, OX7 5GZ")) == (
        "car",
    )
    assert effective_acceptable_modes(PlaceOfInterest("Primary School", "")) == ("walk",)
    assert effective_acceptable_modes(PlaceOfInterest("Gym", "High Street")) == ("transit", "car", "walk")
    # explicit modes always win — never overridden by the rule
    assert effective_acceptable_modes(PlaceOfInterest("Pimlico", "x", acceptable_modes=("walk", "transit"))) == (
        "walk",
        "transit",
    )


def test_effective_editable_by_defaults():
    """Unset editable_by defaults to self for adults and ALL adults for
    children; explicit values always win."""
    from houses.model.domain import Person, effective_editable_by

    simon = Person("Simon", has_car=True)
    lorena = Person("Lorena", has_car=False)
    george = Person("George", has_car=False, is_child=True)
    family = [simon, lorena, george]
    assert effective_editable_by(simon, family) == ("Simon",)
    assert effective_editable_by(george, family) == ("Simon", "Lorena")
    explicit = Person("George", has_car=False, is_child=True, editable_by=("Lorena",))
    assert effective_editable_by(explicit, family) == ("Lorena",)


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

class TestSettingsWriteGuard:
    """The settings nodes hold real family data — a stray script or REPL
    kernel (no pytest isolation, not the uvicorn app) must not silently
    replace them.  Deliberate data fixes opt in explicitly."""

    def _non_app_env(self, monkeypatch, *, script_ok=False, app=False):
        import dag.persistence as per
        import houses.nodes.settings as settings_mod

        monkeypatch.setattr(settings_mod, "_app_mode", False)
        monkeypatch.delenv("HOUSES_SCRIPTS_MAY_WRITE", raising=False)
        if script_ok:
            monkeypatch.setenv("HOUSES_SCRIPTS_MAY_WRITE", "1")
        if app:
            settings_mod.set_app_mode()
        monkeypatch.setattr(per, "testing", False)

    def test_guard_blocks_unapproved_script_writes(self, monkeypatch):
        import pytest

        from houses.nodes.settings import guard_settings_write

        self._non_app_env(monkeypatch)
        with pytest.raises(RuntimeError):
            guard_settings_write()

    def test_guard_allows_explicit_script_opt_in(self, monkeypatch):
        from houses.nodes.settings import guard_settings_write

        self._non_app_env(monkeypatch, script_ok=True)
        guard_settings_write()  # must not raise

    def test_guard_allows_the_app_process(self, monkeypatch):
        from houses.nodes.settings import guard_settings_write

        self._non_app_env(monkeypatch, app=True)
        guard_settings_write()  # must not raise

    def test_settings_node_push_blocked_outside_the_app(self, monkeypatch):
        import pytest

        from houses.model.domain import Person
        from houses.nodes.settings import SettingsNode

        self._non_app_env(monkeypatch)
        node = SettingsNode("persons", list[Person])
        with pytest.raises(RuntimeError):
            node.push([Person("Simon", has_car=True)], "user")

def test_effective_selling_home_inference_and_override():
    """Unset selling_home infers from whether home values exist; an
    explicit value always wins (P7: states are explicit, inference is
    only the migration)."""
    from money import Money

    from houses.model.domain import Person, effective_selling_home

    simon = Person("Simon", has_car=True, home_sale_price=Money("550000", "GBP"),
                   outstanding_mortgage=Money("373000", "GBP"))
    ashby = Person("Ashby", has_car=True, cash_contribution=Money("300000", "GBP"))
    assert effective_selling_home(simon) is True   # home values -> selling
    assert effective_selling_home(ashby) is False  # zeroed home -> cash only
    explicit_off = Person("Simon2", has_car=True, home_sale_price=Money("550000", "GBP"), selling_home=False)
    assert effective_selling_home(explicit_off) is False
    explicit_on = Person("Ashby2", has_car=True, cash_contribution=Money("300000", "GBP"), selling_home=True)
    assert effective_selling_home(explicit_on) is True


def test_default_persons_selling_home_flags():
    """Defaults make the toggle explicit: Simon sells a home, everyone
    else is cash-only (Ashby is the no-house exemplar)."""
    persons = make_default_persons()
    by_name = {p.name: p for p in persons}
    assert by_name["Simon"].selling_home is True
    for other in ("Lorena", "Ashby", "George"):
        assert by_name[other].selling_home is False, f"{other} should be explicit not-selling"

def test_effective_selling_home_tolerates_legacy_money_shapes():
    """Legacy persons may carry bare-number or dict money values — the
    inference must not crash on .amount (which would freeze the cascade)."""
    from houses.model.domain import Person, effective_selling_home

    # bare numbers (not Money) construct fine — the dataclass doesn't
    # validate; the inference must tolerate the shape
    legacy = Person("Legacy", has_car=True, home_sale_price=500000)  # type: ignore[arg-type]
    assert effective_selling_home(legacy) is True
    zero = Person("Zero", has_car=True, home_sale_price=0, outstanding_mortgage=0)  # type: ignore[arg-type]
    assert effective_selling_home(zero) is False
