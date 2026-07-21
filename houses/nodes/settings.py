"""Settings source factories.

Settings sources are NOT module-level objects — they're created eagerly
by ``Services.__init__`` in ``houses/services.py``.  Tests create them
from the in-memory DB by calling ``make_services``.

This module provides the default-value factories and the persistence
helper used by the production ``Services`` constructor.
"""

from __future__ import annotations

from typing import Any

from money import Money

from houses.config import settings
from houses.model.domain import Person, PlaceOfInterest


def make_default_persons() -> list[Person]:
    """Default set of persons with their commute preferences."""
    return [
        Person(
            name="Simon",
            has_car=True,
            bus_walk_penalty_minutes=20,
            deposit_equity=Money("177000", "GBP"),
            places_of_interest=(
                PlaceOfInterest(label="Pimlico", postcode=settings.simon_postcode, trips_per_week=1, weeks_per_year=46),
                PlaceOfInterest(
                    label="Bracknell", postcode=settings.bracknell_postcode, trips_per_week=1, weeks_per_year=46
                ),
                PlaceOfInterest(label="Dad", postcode="OX7 5GZ", trips_per_week=0, weeks_per_year=46),
            ),
        ),
        Person(
            name="Lorena",
            has_car=False,
            bus_walk_penalty_minutes=15,
            deposit_equity=Money("0", "GBP"),
            places_of_interest=(
                PlaceOfInterest(
                    label="Aldgate", postcode=settings.lorena_postcode, trips_per_week=2, weeks_per_year=46
                ),
            ),
        ),
        Person(
            name="George",
            has_car=False,
            is_child=True,
            acceptable_schools=("mixed", "boys", "girls"),
            places_of_interest=(
                PlaceOfInterest(label="Primary School", postcode="", trips_per_week=5, weeks_per_year=39),
                PlaceOfInterest(label="Secondary School", postcode="", trips_per_week=5, weeks_per_year=39),
            ),
        ),
    ]


def make_default_financials() -> dict[str, Any]:
    return {
        "petrol_mpg": 45,
        "petrol_cost_per_litre": 1.45,
        "current_home_sale_price": 0,
        "current_home_outstanding_mortgage": 0,
        "mortgage_rate": 0.0495,
        "mortgage_term_years": 27,
        "sinking_fund_rate": 0.01,
        "rental_income_monthly": 0,
        "life_insurance_monthly": 150,
        "working_weeks_per_year": 46,
    }


def make_default_thresholds() -> dict[str, dict[str, int]]:
    return {
        "Simon": {"good_max_minutes": 30, "fine_max_minutes": 45},
        "Lorena": {"good_max_minutes": 40, "fine_max_minutes": 60},
    }
