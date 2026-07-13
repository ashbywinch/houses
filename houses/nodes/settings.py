"""Settings source factories.

Settings sources are NOT module-level objects — they're created eagerly
by ``Services.__init__`` in ``houses/services.py``.  Tests create them
from the in-memory DB by calling ``make_services``.

This module provides the default-value factories and the persistence
helper used by the production ``Services`` constructor.
"""

from __future__ import annotations

from typing import Any

from dag.persistence import latest_node_result
from dag.user_input_node import UserInputNode
from houses.config import settings


def make_default_persons() -> list[dict]:
    return [
        {
            "name": "Simon",
            "has_car": True,
            "is_child": False,
            "deposit_equity": None,
            "bus_walk_penalty_minutes": 20,
            "places_of_interest": [
                {"label": "Pimlico", "postcode": settings.simon_postcode,
                 "trips_per_week": 1, "weeks_per_year": 46},
                {"label": "Bracknell", "postcode": settings.bracknell_postcode,
                 "trips_per_week": 1, "weeks_per_year": 46},
                {"label": "Dad", "postcode": "OX7 5GZ",
                 "trips_per_week": 0, "weeks_per_year": 46},
            ],
        },
        {
            "name": "Lorena",
            "has_car": False,
            "is_child": False,
            "deposit_equity": None,
            "bus_walk_penalty_minutes": 15,
            "places_of_interest": [
                {"label": "Aldgate", "postcode": settings.lorena_postcode,
                 "trips_per_week": 2, "weeks_per_year": 46},
            ],
        },
        {
            "name": "George",
            "has_car": False,
            "is_child": True,
            "deposit_equity": None,
            "bus_walk_penalty_minutes": 30,
            "places_of_interest": [
                {"label": "Primary School", "postcode": "",
                 "trips_per_week": 5, "weeks_per_year": 39},
                {"label": "Secondary School", "postcode": "",
                 "trips_per_week": 5, "weeks_per_year": 39},
            ],
        },
    ]


def make_default_financials() -> dict[str, Any]:
    return {
        "current_home_sale_price": 0,
        "current_home_outstanding_mortgage": 0,
        "mortgage_rate": 0.045,
        "mortgage_term_years": 30,
        "sinking_fund_rate": 0.01,
        "rental_income_monthly": 0,
        "life_insurance_monthly": 0,
        "working_weeks_per_year": 46,
    }


def make_default_thresholds() -> dict[str, dict[str, int]]:
    return {
        "Simon": {"good_max_minutes": 30, "fine_max_minutes": 45},
        "Lorena": {"good_max_minutes": 40, "fine_max_minutes": 60},
    }

