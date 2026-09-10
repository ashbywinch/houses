"""A shrinking household doc must alarm LOUDLY at CRITICAL with the write
path and exactly what vanished — the 2026-09-09 incident (persons doc
shrank to Simon/Pimlico; Ashby, George and two POIs vanished silently)
was discovered only because figures looked wrong weeks later."""

from __future__ import annotations

from houses.model.domain import Person, PlaceOfInterest
from houses.services_provider import get_services


def _poi(label: str) -> PlaceOfInterest:
    return PlaceOfInterest(label=label, address="", trips_per_week=1, weeks_per_year=46)


def test_push_that_loses_persons_or_pois_alarms_at_critical(caplog):
    svc = get_services()
    full = [
        Person(
            name="Simon",
            has_car=True,
            places_of_interest=(_poi("Pimlico"), _poi("Bracknell")),
        ),
        Person(name="Ashby", has_car=True),
    ]
    svc.persons_source.push(full, "user")

    shrunk = [
        Person(
            name="Simon",
            has_car=True,
            places_of_interest=(_poi("Pimlico"),),
        ),
    ]
    with caplog.at_level("CRITICAL", logger="houses.nodes.settings"):
        # The seeding push already alarmed against the factory defaults —
        # clear it so assertions see ONLY the shrink event.
        caplog.clear()
        svc.persons_source.push(shrunk, "what-if")

    hits = [
        r
        for r in caplog.records
        if r.levelno >= 50 and "HOUSEHOLD SHRUNK" in r.getMessage()
    ]
    assert hits, "a household shrink must alarm at CRITICAL level"
    message = hits[0].getMessage()
    assert "Ashby" in message, f"the alarm must name the vanished person: {message}"
    assert "Bracknell" in message, f"the alarm must name the vanished destination: {message}"
    assert "what-if" in message, f"the alarm must name the write path: {message}"


def test_no_alarm_when_the_household_only_changes_values(caplog):
    """Trips/car/MPG edits are normal: no alarm, no noise."""
    svc = get_services()
    full = [
        Person(
            name="Simon",
            has_car=True,
            places_of_interest=(_poi("Pimlico"),),
        ),
    ]
    svc.persons_source.push(full, "user")

    repriced = [
        Person(
            name="Simon",
            has_car=True,
            places_of_interest=(PlaceOfInterest(label="Pimlico", address="", trips_per_week=3, weeks_per_year=46),),
        ),
        Person(name="Ashby", has_car=True),
    ]
    # The seeding push alarms against the factory defaults — clear it so
    # the window only sees the value-only change.
    caplog.clear()
    with caplog.at_level("CRITICAL", logger="houses.nodes.settings"):
        svc.persons_source.push(repriced, "what-if")

    hits = [r for r in caplog.records if "HOUSEHOLD SHRUNK" in r.getMessage()]
    assert not hits, "a value-only change is not a shrink"
