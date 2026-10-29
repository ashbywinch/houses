"""The list card's commute projection.

The index wire carries only what the card renders. A commute's detail-page
data — the whole Person record, the destination's frequency/modes, the route
legs, the planned origin — must not reach the list (docs/api.md →
``GET /api/properties/all``).
"""

from __future__ import annotations

import pytest
from money import Money
from pint import Quantity

from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.model.domain import Commute, HomeCoOwner, Person, PlaceOfInterest
from houses.nodes.commute_card import CommuteCard, card_wire


def _full_commute(*, is_child: bool = False) -> Commute:
    """A commute carrying everything the detail page shows and the card
    does not."""
    person = Person(
        name="Simon",
        has_car=True,
        email="simon@example.invalid",
        home_co_owners=(HomeCoOwner(name="Lorena", share=50),),
        rent_paid_monthly=Money("1200.00", "GBP"),
        places_of_interest=(PlaceOfInterest(label="Office", address="SW1P 1AA"),),
    )
    destination = PlaceOfInterest(
        label="Office",
        address="SW1P 1AA",
        trips_per_week=3,
        weeks_per_year=46,
        acceptable_modes=("transit", "car"),
    )
    return Commute(
        person=person,
        label="Office",
        destination=destination,
        duration=Quantity(32, "minute"),
        daily_cost=Money("4.50", "GBP"),
        mode="transit",
        _details=(
            CostGroup(
                legs=(JourneyLeg(mode=LegMode.TUBE, duration=Quantity(10, "minute")),),
                operator="TfL",
                cost=Money("4.50", "GBP"),
            ),
        ),
        is_child=is_child,
        origin="51.5187,-0.7228",
    )


def test_card_keeps_only_the_rendered_fields() -> None:
    wire = CommuteCard.from_commute(_full_commute()).to_dict()

    assert set(wire) == {"label", "person", "destination", "duration", "daily_cost", "mode", "is_child"}
    assert wire["person"] == {"name": "Simon"}
    assert wire["destination"] == {"address": "SW1P 1AA"}
    assert wire["duration"] == {"value": 32, "unit": "minute"}
    assert wire["daily_cost"] == {"amount": "4.50", "currency": "GBP"}
    assert wire["mode"] == "transit"


@pytest.mark.parametrize(("is_child", "expected"), [(False, False), (True, True)])
def test_card_states_the_child_flag(is_child: bool, expected: bool) -> None:
    wire = CommuteCard.from_commute(_full_commute(is_child=is_child)).to_dict()

    assert wire["is_child"] is expected


def test_card_wire_states_no_value_for_an_empty_row() -> None:
    """A pending/impossible row has no commute value to project."""
    assert card_wire(None) is None


def test_card_wire_refuses_a_non_commute_value() -> None:
    """A succeeded row whose value is not a Commute is a broken invariant —
    it must surface, never ship a silently blank card."""
    with pytest.raises(TypeError):
        card_wire({"label": "Bracknell"})
