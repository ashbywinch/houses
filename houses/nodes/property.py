from __future__ import annotations

from typing import Any

from dag.signals import Signal, Slot
from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint
from houses.nodes.area import TownDescNode, TownNode, WalkabilityNode
from houses.nodes.bus import BodsFareNode, BusLegAugmentNode, BusRouteNode
from houses.nodes.commute import CommuteSelectorNode
from houses.nodes.epc_node import CouncilTaxNode, EpcNode
from houses.nodes.geocode import GeocodeNode
from houses.nodes.location import BestAddressNode, BestLocationNode
from houses.nodes.monthly_costs import (
    CommuteBreakdownNode,
    MonthlyMortgagePaymentNode,
    StampDutyNode,
    TotalMonthlyHousingCostNode,
    YearlySinkingFundNode,
)
from houses.nodes.park_and_ride import ParkAndRideAugmentNode
from houses.nodes.petrol import PetrolCostAugmentNode
from houses.nodes.schools import PrimarySchoolNode, SchoolLocationNode, SecondarySchoolNode
from houses.nodes.transit import TransitNode, WalkLegCheckNode
from houses.services_provider import get_services


class PropertyNodes:
    """Holds all DAG node references for one property.

    Creates UserInputNodes for user-owned and enrichment data, DerivedNodes
    for derived values, and wires signal propagation.
    """

    def __init__(self, rid: str) -> None:
        self.rid = rid
        self.changed = Signal()
        self._svc = get_services()

        # ── Source Nodes (user-entered / scraped) ──────────────────────
        self.rightmove_url = UserInputNode[str](f"{rid}/rightmove_url", str)
        self.rightmove_address = UserInputNode[str](f"{rid}/rightmove_address", str)
        self.rightmove_bedrooms = UserInputNode[str](f"{rid}/rightmove_bedrooms", str)
        self.rightmove_price = UserInputNode[str](f"{rid}/rightmove_price", str)
        self.rightmove_location = UserInputNode[GeoPoint](f"{rid}/rightmove_location", GeoPoint)
        self.precise_location = UserInputNode[GeoPoint](f"{rid}/precise_location", GeoPoint)
        self.corrected_address = UserInputNode[str](f"{rid}/corrected_address", str)
        self.user_entered_address = UserInputNode[str](f"{rid}/user_entered_address", str)
        self.postcode = UserInputNode[str](f"{rid}/postcode", str)

        # Comments from View tab
        self.comment_status = UserInputNode[str](f"{rid}/status", str)
        self.comment_status_reason = UserInputNode[str](f"{rid}/status_reason", str)
        self.comment_group_notes = UserInputNode[str](f"{rid}/group_notes", str)
        self.comment_ashby_comments = UserInputNode[str](f"{rid}/ashby_comments", str)
        self.comment_ashby_works = UserInputNode[float](f"{rid}/ashby_works", float)
        self.comment_design_needed = UserInputNode[str](f"{rid}/design_needed", str)
        self.comment_planning_needed = UserInputNode[str](f"{rid}/planning_needed", str)

        # ── Location DerivedNodes ─────────────────────────────────────
        self.best_address = BestAddressNode(
            f"{rid}/best_address",
            user_entered_address=self.user_entered_address,
            corrected_address=self.corrected_address,
            rightmove_address=self.rightmove_address,
        )
        self.geocode = GeocodeNode(
            f"{rid}/geocode",
            best_address=self.best_address,
        )
        self.best_location = BestLocationNode(
            f"{rid}/best_location",
            precise_location=self.precise_location,
            rightmove_location=self.rightmove_location,
            best_address=self.best_address,
            geocode=self.geocode,
        )

        # ── Enrichment Nodes ────────────────────────────────────────────
        self.epc = EpcNode(
            f"{rid}/epc",
            best_address=self.best_address,
            postcode_node=self.postcode,
        )
        self.council_tax = CouncilTaxNode(
            f"{rid}/council_tax",
            best_address=self.best_address,
            postcode_node=self.postcode,
        )
        self.walkability = WalkabilityNode(
            f"{rid}/walkability",
            best_location=self.best_location,
            best_address=self.best_address,
        )
        self.town_desc = TownDescNode(
            f"{rid}/town_desc",
            best_location=self.best_location,
        )
        self.town_name = TownNode(
            f"{rid}/town_name",
            best_address=self.best_address,
        )

        # ── School Nodes ───────────────────────────────────────────────
        # Find the first child person's acceptable school types
        _school_acceptable = ("mixed",)
        for p in self._svc.persons_source._value or []:
            if p.is_child:
                _school_acceptable = p.acceptable_schools
                break
        self.primary_school = PrimarySchoolNode(
            f"{rid}/primary_school",
            best_location=self.best_location,
            best_address=self.best_address,
            acceptable=_school_acceptable,
        )
        self.secondary_school = SecondarySchoolNode(
            f"{rid}/secondary_school",
            best_location=self.best_location,
            best_address=self.best_address,
            acceptable=_school_acceptable,
        )
        # ── Commute Pipeline ────────────────────────────────────────────
        self._build_commute_pipeline()

        # ── Monthly Cost Calculation Nodes ──────────────────────────────
        self.stamp_duty = StampDutyNode(
            f"{rid}/stamp_duty",
            rightmove_price=self.rightmove_price,
        )
        self.monthly_mortgage = MonthlyMortgagePaymentNode(
            f"{rid}/monthly_mortgage",
            rightmove_price=self.rightmove_price,
            stamp_duty_node=self.stamp_duty,
            persons_source=self._svc.persons_source,
            financial_source=self._svc.financial_source,
        )
        self.yearly_sinking_fund = YearlySinkingFundNode(
            f"{rid}/yearly_sinking_fund",
            rightmove_price=self.rightmove_price,
            financial_source=self._svc.financial_source,
        )
        self.total_monthly_cost = TotalMonthlyHousingCostNode(
            f"{rid}/total_monthly_cost",
            monthly_mortgage_node=self.monthly_mortgage,
            yearly_sinking_fund_node=self.yearly_sinking_fund,
            financial_source=self._svc.financial_source,
            commute_breakdown_node=self.commute_breakdown,
            council_tax_node=self.council_tax,
        )

        # ── Signal wiring ──────────────────────────────────────────────
        all_nodes: list = [
            self.rightmove_url,
            self.rightmove_address,
            self.rightmove_bedrooms,
            self.rightmove_price,
            self.rightmove_location,
            self.precise_location,
            self.corrected_address,
            self.user_entered_address,
            self.postcode,
            self.comment_status,
            self.comment_status_reason,
            self.comment_group_notes,
            self.comment_ashby_comments,
            self.comment_ashby_works,
            self.comment_design_needed,
            self.comment_planning_needed,
        ]
        self._slots: list[Slot] = []
        for node in all_nodes:
            slot = Slot(self._on_node_changed)
            self._slots.append(slot)
            node.changed.connect(slot)

    def _build_commute_pipeline(self) -> None:
        self._transit_nodes = []
        self._bus_augment_nodes = []
        self.commute_selectors = {}

        for p_info in self._svc.persons_source._value or []:
            p_name = p_info.name
            pois = p_info.places_of_interest
            for poi in pois:
                label = poi.label
                postcode = poi.postcode
                key = f"{p_name}/{label}"

                is_child = p_info.is_child
                if is_child:
                    school_node = (
                        self.primary_school
                        if "Primary" in label
                        else self.secondary_school
                        if "Secondary" in label
                        else None
                    )
                    if school_node is None:
                        continue
                    poi_src = SchoolLocationNode(
                        f"{self.rid}/{key}/poi",
                        school_node=school_node,
                    )
                else:
                    poi_src = UserInputNode[str](f"{self.rid}/{key}/poi", str)
                    poi_src.push(postcode, "persons_source")

                transit_node = TransitNode(
                    f"{self.rid}/{key}/computed_transit",
                    best_location=self.best_location,
                    poi=poi_src,
                    has_car=p_info.has_car,
                    max_walk=p_info.bus_walk_penalty_minutes,
                )
                self._transit_nodes.append(transit_node)

                park_and_ride = ParkAndRideAugmentNode(
                    f"{self.rid}/{key}/park_and_ride",
                    transit_node=transit_node,
                    best_location=self.best_location,
                    postcode_node=self.postcode,
                    has_car=p_info.has_car,
                    max_walk=p_info.bus_walk_penalty_minutes,
                )

                petrol_cost = PetrolCostAugmentNode(
                    f"{self.rid}/{key}/petrol_cost",
                    commute_node=park_and_ride,
                    financial_source=self._svc.financial_source,
                )

                walk_check = WalkLegCheckNode(
                    f"{self.rid}/{key}/walk_check",
                    transit_node=transit_node,
                    max_walk=p_info.bus_walk_penalty_minutes,
                )

                bus_route = BusRouteNode(
                    f"{self.rid}/{key}/bus_route",
                    best_location=self.best_location,
                    walk_leg_check_node=walk_check,
                    transit_node=transit_node,
                )

                bods_fare = BodsFareNode(
                    f"{self.rid}/{key}/bods_fare",
                    bus_route_node=bus_route,
                )

                bus_augment = BusLegAugmentNode(
                    f"{self.rid}/{key}/bus_augment",
                    transit_node=transit_node,
                    walk_leg_check_node=walk_check,
                    bus_route_node=bus_route,
                    bods_fare_node=bods_fare,
                )
                self._bus_augment_nodes.append(bus_augment)

                # Create a RailFareNode for non-child commutes to apply NR fares
                rail_fare_node = None
                if not is_child:
                    from houses.nodes.commute import RailFareNode

                    rail_fare_node = RailFareNode(
                        f"{self.rid}/{key}/rail_fare",
                        transit_result=transit_node,
                        best_location=self.best_location,
                    )

                selector = CommuteSelectorNode(
                    f"{self.rid}/{key}/commute",
                    origin=self.best_location,
                    poi=poi_src,
                    transit_result=petrol_cost,
                    bus_result=bus_augment,
                    walk_leg_check=walk_check,
                    is_child=is_child,
                    rail_fare_node=rail_fare_node,
                )
                self.commute_selectors[key] = selector

        self.commute_breakdown = CommuteBreakdownNode(
            f"{self.rid}/commute_breakdown",
            commute_selectors=self.commute_selectors,
            persons_source=self._svc.persons_source,
        )

    def _on_node_changed(self) -> None:
        self.changed.emit()

    async def to_json(self) -> dict[str, Any]:
        return {
            "rid": self.rid,
            "best_address": await self.best_address.to_json(),
            "best_location": await self.best_location.to_json(),
            "rightmove_url": await self.rightmove_url.to_json(),
            "rightmove_price": await self.rightmove_price.to_json(),
            "rightmove_bedrooms": await self.rightmove_bedrooms.to_json(),
            "postcode": await self.postcode.to_json(),
        }

    async def to_json_summary(self) -> dict[str, Any]:
        return {
            "rid": self.rid,
            "best_address": await self.best_address.to_json(),
            "best_location": await self.best_location.to_json(),
            "rightmove_price": await self.rightmove_price.to_json(),
            "rightmove_bedrooms": await self.rightmove_bedrooms.to_json(),
            "total_monthly_cost": await self.total_monthly_cost.to_json(),
            "town_name": await self.town_name.to_json(),
            "commutes": {k: {"commute": await v.to_json()} for k, v in self.commute_selectors.items()},
            "schools": {
                "primary": {
                    "school": await self.primary_school.to_json(),
                },
                "secondary": {
                    "school": await self.secondary_school.to_json(),
                },
            },
            "walkability": await self.walkability.to_json(),
        }

    async def _monthly_sinking(self) -> dict:
        yearly = await self.yearly_sinking_fund.to_json()
        if yearly.get("status") == "succeeded" and yearly.get("value") is not None:
            yearly_value = yearly["value"]
            monthly = round(yearly_value / 12 * 2 / 3, 2)
            return {
                "status": "succeeded",
                "value": monthly,
                "provenance": {"label": "formula:monthly_sinking", "description": f"{yearly_value}/12*2/3"},
            }
        return yearly

    async def to_json_detail(self) -> dict[str, Any]:
        return {
            "rid": self.rid,
            "best_address": await self.best_address.to_json(),
            "user_entered_address": await self.user_entered_address.to_json(),
            "rightmove_url": await self.rightmove_url.to_json(),
            "rightmove_price": await self.rightmove_price.to_json(),
            "rightmove_bedrooms": await self.rightmove_bedrooms.to_json(),
            "town_name": await self.town_name.to_json(),
            "epc": await self.epc.to_json(),
            "location": {
                "best_location": await self.best_location.to_json(),
                "geocode": await self.geocode.to_json(),
                "rightmove_location": await self.rightmove_location.to_json(),
                "precise_location": await self.precise_location.to_json(),
            },
            "commutes": {k: await v.to_json() for k, v in self.commute_selectors.items()},
            "schools": {
                "primary": {
                    "school": await self.primary_school.to_json(),
                },
                "secondary": {
                    "school": await self.secondary_school.to_json(),
                },
            },
            "affordability": {
                "stamp_duty": await self.stamp_duty.to_json(),
                "council_tax": await self.council_tax.to_json(),
                "monthly_mortgage": await self.monthly_mortgage.to_json(),
                "monthly_sinking_fund": await self._monthly_sinking(),
                "monthly_commute_cost": await self.commute_breakdown.to_json(),
                "total_monthly_housing_cost": await self.total_monthly_cost.to_json(),
            },
            "area": {
                "walkability": await self.walkability.to_json(),
                "town_description": await self.town_desc.to_json(),
            },
            "comments": {
                "status": await self.comment_status.to_json(),
                "status_reason": await self.comment_status_reason.to_json(),
                "group_notes": await self.comment_group_notes.to_json(),
                "ashby_comments": await self.comment_ashby_comments.to_json(),
                "ashby_works_estimate": await self.comment_ashby_works.to_json(),
                "design_needed": await self.comment_design_needed.to_json(),
                "planning_needed": await self.comment_planning_needed.to_json(),
            },
            "settings": {
                "persons": await self._svc.persons_source.to_json(),
                "financial": await self._svc.financial_source.to_json(),
            },
        }
