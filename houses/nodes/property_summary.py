"""One property as the list and the live-update push report it."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypedDict

from pydantic import Field

from dag.persistence import property_created_at
from houses.nodes.school_entry import SchoolEntry
from houses.nodes.schools_block import SchoolsBlock
from houses.nodes.triage_block import TriageBlock
from serialization import SerializableAsDict

if TYPE_CHECKING:
    from houses.nodes.property_nodes import PropertyNodes


class _Freshness(TypedDict, total=False):
    """Wire shape of the freshness block."""

    property_added_at: str | None


class PropertySummary(SerializableAsDict):
    """One property as the list and the live-update push report it."""

    rid: str = ""
    best_address: Any = None
    best_location: Any = None
    rightmove_price: Any = None
    rightmove_bedrooms: Any = None
    group_monthly_cost: Any = None
    town_name: Any = None
    commutes: dict[str, Mapping[str, Any]] = Field(default_factory=dict)
    schools: SchoolsBlock = Field(default_factory=SchoolsBlock)
    walkability: Any = None
    epc: Any = None
    triage: TriageBlock = Field(default_factory=TriageBlock)
    freshness: _Freshness = Field(default_factory=_Freshness)


async def property_summary(nodes: PropertyNodes) -> PropertySummary:
    """Build the summary document from a property's node container.

    The commute entries are the wire projection of the live selectors: only
    a destination that actually happens gets an entry (trips and weeks both
    above zero), and its key carries the person's NAME, never their id.
    """
    return PropertySummary(
        rid=nodes.rid,
        best_address=await nodes.best_address.to_json_value(),
        best_location=await nodes.best_location.to_json_value(),
        rightmove_price=await nodes.rightmove_price.to_json_value(),
        rightmove_bedrooms=await nodes.rightmove_bedrooms.to_json_value(),
        group_monthly_cost=await nodes.group_monthly_cost.to_json_value(),
        town_name=await nodes.town_name.to_json_value(),
        commutes={
            nodes.commute_wire_key(key, nodes.commute_wire_map()): {"commute": await selector.to_json_value()}
            for key, selector in nodes.commute_selectors.items()
            if key in nodes.commuted_destinations()
        },
        schools=SchoolsBlock(
            primary=SchoolEntry(school=await nodes.primary_school.to_json_value()),
            secondary=SchoolEntry(school=await nodes.secondary_school.to_json_value()),
        ),
        walkability=await nodes.walkability.to_json_value(),
        epc=await nodes.epc.to_json_value(),
        triage=TriageBlock(
            favourite=await nodes.favourite.to_json_value(),
            dismissed=await nodes.dismissed.to_json_value(),
            is_viewed=await nodes.is_viewed.to_json_value(),
            user_notes=await nodes.user_notes.to_json_value(),
            triage_status=await nodes.triage_status.to_json_value(),
        ),
        freshness=_Freshness(property_added_at=property_created_at(nodes.rid)),
    )
