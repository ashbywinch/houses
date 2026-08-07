"""Settings source factories.

Settings sources are NOT module-level objects — they're created eagerly
by ``Services.__init__`` in ``houses/services.py``.  Tests create them
from the in-memory DB by calling ``make_services``.

This module provides the default-value factories and the persistence
helper used by the production ``Services`` constructor.
"""

from __future__ import annotations

import os
from typing import Any

from money import Money
from pint import Quantity

from dag import persistence as _persistence
from dag.user_input_node import UserInputNode
from houses.config import settings
from houses.model.domain import Person, PlaceOfInterest

_app_mode = False


def set_app_mode() -> None:
    """Mark this process as the running app.

    Called from the FastAPI lifespan — every real uvicorn worker (and
    reloader respawn) runs the lifespan; an ad-hoc script or REPL kernel
    that merely imports the app modules does not.
    """
    global _app_mode
    _app_mode = True


def guard_settings_write() -> None:
    """Block silent settings writes from non-app, non-test processes.

    The settings nodes (persons/financial/thresholds) hold real family
    data.  A stray script or REPL kernel has no pytest isolation and is
    not the running app, yet can silently replace the whole config —
    which is exactly how the family emails were wiped.  Deliberate
    data-fix scripts must opt in explicitly.
    """
    if _persistence.testing:
        return  # pytest isolation fixtures
    if _app_mode:
        return  # the running uvicorn app (lifespan set the flag)
    if os.environ.get("HOUSES_SCRIPTS_MAY_WRITE") == "1":
        return  # explicit opt-in for deliberate data-fix scripts
    raise RuntimeError(
        "Refusing to write settings from a non-app process. "
        "Set HOUSES_SCRIPTS_MAY_WRITE=1 to run a deliberate data-fix script."
    )


class SettingsNode(UserInputNode):
    """A settings input node whose writes are guarded — only the app,
    pytest, or an explicitly opted-in script may change family data."""

    def push(self, value: Any, source_label: str = "") -> None:
        guard_settings_write()
        super().push(value, source_label)


def make_default_persons() -> list[Person]:
    """Default set of persons with their commute preferences."""
    return [
        Person(
            name="Simon",
            email="",
            is_superuser=False,
            has_car=True,
            selling_home=True,
            bus_walk_penalty=Quantity(20, "minute"),
            home_sale_price=Money("550000", "GBP"),
            outstanding_mortgage=Money("373000", "GBP"),
            life_insurance_monthly=Money("150", "GBP"),
            places_of_interest=(
                PlaceOfInterest(
                    label="Pimlico",
                    address=settings.simon_destination,
                    trips_per_week=1,
                    weeks_per_year=46,
                    acceptable_modes=("transit",),
                ),
                PlaceOfInterest(
                    label="Bracknell",
                    address="Waite House, Doncastle Road, Bracknell, Berkshire RG12 8YA",
                    trips_per_week=1,
                    weeks_per_year=46,
                    acceptable_modes=("car",),
                ),
                PlaceOfInterest(
                    label="Dad",
                    address="Flat 37, Watson Place, Trinity Road, Chipping Norton OX7 5GZ",
                    trips_per_week=1,
                    weeks_per_year=46,
                    acceptable_modes=("car",),
                ),
            ),
        ),
        Person(
            name="Lorena",
            email="",
            is_superuser=False,
            selling_home=False,
            has_car=False,
            bus_walk_penalty=Quantity(15, "minute"),
            places_of_interest=(
                PlaceOfInterest(
                    label="Aldgate",
                    address=settings.lorena_destination,
                    trips_per_week=2,
                    weeks_per_year=46,
                    acceptable_modes=("transit",),
                ),
            ),
        ),
        Person(
            name="Ashby",
            email="",
            is_superuser=False,
            selling_home=False,
            has_car=True,
            bus_walk_penalty=Quantity(10, "minute"),
            cash_contribution=Money("300000", "GBP"),
            works_estimate_required=True,
            places_of_interest=(),
        ),
        Person(
            name="George",
            email="",
            is_superuser=False,
            selling_home=False,
            has_car=False,
            is_child=True,
            acceptable_schools=("mixed", "boys", "girls"),
            editable_by=("Simon", "Lorena", "Ashby"),
            places_of_interest=(
                PlaceOfInterest(
                    label="Primary School", address="", trips_per_week=5, weeks_per_year=39, acceptable_modes=("walk",)
                ),
                PlaceOfInterest(
                    label="Secondary School",
                    address="",
                    trips_per_week=5,
                    weeks_per_year=39,
                    acceptable_modes=("walk",),
                ),
            ),
        ),
    ]


def make_default_thresholds() -> dict[str, dict[str, int]]:
    return {
        "Simon": {"good_max_minutes": 30, "fine_max_minutes": 45},
        "Lorena": {"good_max_minutes": 40, "fine_max_minutes": 60},
    }
