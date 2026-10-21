"""One property's full detail document — every section the page renders."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from houses.nodes.school_entry import SchoolEntry
from houses.nodes.schools_block import SchoolsBlock
from houses.nodes.settings_node import aggregate_dict
from houses.nodes.triage_block import TriageBlock
from serialization import SerializableAsDict

if TYPE_CHECKING:
    from houses.nodes.property_nodes import PropertyNodes
    from houses.services import Services


class _Location(TypedDict, total=False):
    """Wire shape of the detail location block."""

    best_location: Any
    geocode: Any
    rightmove_location: Any
    precise_location: Any


class _Affordability(TypedDict, total=False):
    """Wire shape of the detail affordability block."""

    stamp_duty: Any
    council_tax: Any
    works_estimates: Any
    total_works: Any
    total_equity: Any
    life_insurance_total: Any
    mortgage_required: Any
    monthly_mortgage: Any
    monthly_sinking_fund: Any
    rental_income: Any
    monthly_commute_cost: Any
    group_monthly_cost: Any


class _CouncilTaxApportionment(TypedDict, total=False):
    """Wire shape of the council-tax apportionment block."""

    main_payers: Any
    annexe_payers: Any
    ignored: Any


class _Area(TypedDict, total=False):
    """Wire shape of the detail area block."""

    walkability: Any
    town_description: Any


class _Comments(TypedDict, total=False):
    """Wire shape of the detail comments block."""

    status: Any
    status_reason: Any
    group_notes: Any
    ashby_comments: Any
    design_needed: Any
    planning_needed: Any


class _SettingsFinancial(TypedDict, total=False):
    """The financial settings block: status plus the aggregate value."""

    status: str
    value: Any


class _SettingsBlock(TypedDict, total=False):
    """The settings section of the detail payload."""

    persons: Any
    financial: _SettingsFinancial


class PropertyDetail(SerializableAsDict):
    """One property's full detail document — every section the page renders."""

    rid: str
    best_address: Any
    user_entered_address: Any
    rightmove_url: Any
    rightmove_price: Any
    rightmove_bedrooms: Any
    town_name: Any
    epc: Any
    location: _Location
    commutes: dict[str, Any]
    schools: SchoolsBlock
    affordability: _Affordability
    council_tax_apportionment: _CouncilTaxApportionment
    area: _Area
    triage: TriageBlock
    comments: _Comments
    settings: _SettingsBlock


async def property_detail(nodes: PropertyNodes, svc: Services) -> PropertyDetail:
    """Build the detail document from a property's node container.

    The commute entries follow the summary's projection (name/label keys,
    one entry per destination that happens); the settings section carries
    the GLOBAL persons and financial aggregates, so the services container
    is a parameter.
    """
    return PropertyDetail(
        rid=nodes.rid,
        best_address=await nodes.best_address.to_json_value(),
        user_entered_address=await nodes.user_entered_address.to_json_value(),
        rightmove_url=await nodes.rightmove_url.to_json_value(),
        rightmove_price=await nodes.rightmove_price.to_json_value(),
        rightmove_bedrooms=await nodes.rightmove_bedrooms.to_json_value(),
        town_name=await nodes.town_name.to_json_value(),
        epc=await nodes.epc.to_json_value(),
        location=_Location(
            best_location=await nodes.best_location.to_json_value(),
            geocode=await nodes.geocode.to_json_value(),
            rightmove_location=await nodes.rightmove_location.to_json_value(),
            precise_location=await nodes.precise_location.to_json_value(),
        ),
        commutes={
            nodes.commute_wire_key(key, nodes.commute_wire_map()): await selector.to_json_value()
            for key, selector in nodes.commute_selectors.items()
            if key in nodes.commuted_destinations()
        },
        schools=SchoolsBlock(
            primary=SchoolEntry(school=await nodes.primary_school.to_json_value()),
            secondary=SchoolEntry(school=await nodes.secondary_school.to_json_value()),
        ),
        affordability=_Affordability(
            stamp_duty=await nodes.stamp_duty.to_json_value(),
            council_tax=await nodes.council_tax.to_json_value(),
            works_estimates=await nodes.works_estimates.to_json_value(),
            total_works=await nodes.total_works.to_json_value(),
            total_equity=await nodes.total_equity.to_json_value(),
            life_insurance_total=await nodes.life_insurance_total.to_json_value(),
            mortgage_required=await nodes.mortgage_required.to_json_value(),
            monthly_mortgage=await nodes.monthly_mortgage.to_json_value(),
            monthly_sinking_fund=await nodes.monthly_sinking_fund.to_json_value(),
            rental_income=await nodes.rental_income.to_json_value(),
            monthly_commute_cost=await nodes.commute_breakdown_json(),
            group_monthly_cost=await nodes.group_monthly_cost.to_json_value(),
        ),
        council_tax_apportionment=_CouncilTaxApportionment(
            main_payers=await nodes.council_tax_payers.to_json_value(),
            annexe_payers=await nodes.annexe_payers.to_json_value(),
            ignored=await nodes.annexe_ignored.to_json_value(),
        ),
        area=_Area(
            walkability=await nodes.walkability.to_json_value(),
            town_description=await nodes.town_desc.to_json_value(),
        ),
        triage=TriageBlock(
            favourite=await nodes.favourite.to_json_value(),
            dismissed=await nodes.dismissed.to_json_value(),
            is_viewed=await nodes.is_viewed.to_json_value(),
            user_notes=await nodes.user_notes.to_json_value(),
            triage_status=await nodes.triage_status.to_json_value(),
        ),
        comments=_Comments(
            status=await nodes.comment_status.to_json_value(),
            status_reason=await nodes.comment_status_reason.to_json_value(),
            group_notes=await nodes.comment_group_notes.to_json_value(),
            ashby_comments=await nodes.comment_ashby_comments.to_json_value(),
            design_needed=await nodes.comment_design_needed.to_json_value(),
            planning_needed=await nodes.comment_planning_needed.to_json_value(),
        ),
        settings=_SettingsBlock(
            persons=await svc.persons_source.to_json_value(),
            financial=_SettingsFinancial(status="succeeded", value=aggregate_dict(svc.setting_nodes)),
        ),
    )
