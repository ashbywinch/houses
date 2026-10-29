"""A commute as the list card states it — only the fields the card renders.

The full commute (the detail page's wire) embeds the whole Person record,
the destination's trips/weeks/modes, every route leg, and the planned
origin. An index load cannot show any of that: the card renders the
person's name, the destination label and address, the duration/cost pill,
and whether the row is a child's. Shipping the rest made commutes 66 % of
the index payload — see docs/api.md → ``GET /api/properties/all``.
"""

from __future__ import annotations

from pydantic import Field

from houses.model.domain import Commute
from houses.nodes.commute_card_destination import CommuteCardDestination
from houses.nodes.commute_card_person import CommuteCardPerson
from serialization import MoneyValue, QuantityValue, SerializableAsDict


class CommuteCard(SerializableAsDict):
    """The commute value the list card renders."""

    label: str = ""
    person: CommuteCardPerson = Field(default_factory=CommuteCardPerson)
    destination: CommuteCardDestination = Field(default_factory=CommuteCardDestination)
    duration: QuantityValue
    daily_cost: MoneyValue
    mode: str = ""
    is_child: bool = False

    @classmethod
    def from_commute(cls, commute: Commute) -> CommuteCard:
        """The card projection of one computed commute."""
        return cls(
            label=commute.label,
            person=CommuteCardPerson(name=commute.person.name),
            destination=CommuteCardDestination(address=commute.destination.address),
            duration=commute.duration,
            daily_cost=commute.daily_cost,
            mode=commute.mode,
            is_child=commute.is_child,
        )


def card_wire(value: object) -> dict | None:
    """The card projection of a commute value.

    ``None`` is reserved for a row that states no value (pending or
    impossible). Any other non-Commute value is a broken invariant, not an
    empty card — raise so it surfaces instead of shipping a silently blank
    row.
    """
    if value is None:
        return None
    if not isinstance(value, Commute):
        raise TypeError(f"card projection expects a Commute, got {type(value).__name__}")
    return CommuteCard.from_commute(value).to_dict()
