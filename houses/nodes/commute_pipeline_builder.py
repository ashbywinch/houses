from __future__ import annotations

from dag.if_then_else import IfThenElseNode
from dag.user_input_node import UserInputNode
from houses.model.domain import Commute
from houses.nodes.bus import BodsFareNode, BusLegAugmentNode, BusRouteNode
from houses.nodes.commute import (
    CommuteSelectorNode,
    _bus_condition,
    _needs_rail_fare,
    commute_input_node,
)
from houses.nodes.commute_breakdown_node import CommuteBreakdownNode
from houses.nodes.park_and_ride import ParkAndRideAugmentNode
from houses.nodes.petrol import PetrolCostAugmentNode
from houses.nodes.schools import SchoolLocationNode
from houses.nodes.transit import DriveNode, TransitNode, WalkLegCheckNode, WalkNode


def build_commute_pipeline(prop) -> None:
    """Build the commute pipeline nodes for a PropertyNodes instance.

    Creates walk, drive, transit, bus, and rail-fare nodes for each person/POI
    combination, wires them through CommuteSelectorNode, and attaches a
    CommuteBreakdownNode aggregator.

    Sets the following attributes on *prop* in place:
        _transit_nodes, _bus_augment_nodes, commute_selectors, commute_breakdown
    """
    prop._transit_nodes = []
    prop._bus_augment_nodes = []
    prop.commute_selectors = {}

    for p_info in prop._svc.persons_source._value or []:
        p_name = p_info.name
        pois = p_info.places_of_interest
        for poi in pois:
            label = poi.label
            postcode = poi.postcode
            key = f"{p_name}/{label}"

            is_child = p_info.is_child
            if is_child:
                school_node = (
                    prop.primary_school
                    if "Primary" in label
                    else prop.secondary_school
                    if "Secondary" in label
                    else None
                )
                if school_node is None:
                    continue
                poi_src = SchoolLocationNode(
                    f"{prop.rid}/{key}/poi",
                    school_node=school_node,
                )
            else:
                poi_src = UserInputNode[str](f"{prop.rid}/{key}/poi", str)
                if poi_src.latest_attempt().pending:
                    poi_src.push(postcode, "persons_source")

            walk_node = WalkNode(
                f"{prop.rid}/{key}/walk",
                best_location=prop.best_location,
                poi=poi_src,
                max_walk=p_info.bus_walk_penalty_minutes,
            )

            drive_node = DriveNode(
                f"{prop.rid}/{key}/drive",
                best_location=prop.best_location,
                poi=poi_src,
                has_car=p_info.has_car,
            )

            transit_node = TransitNode(
                f"{prop.rid}/{key}/computed_transit",
                best_location=prop.best_location,
                poi=poi_src,
                has_car=p_info.has_car,
                max_walk=p_info.bus_walk_penalty_minutes,
            )
            prop._transit_nodes.append(transit_node)

            park_and_ride = ParkAndRideAugmentNode(
                f"{prop.rid}/{key}/park_and_ride",
                transit_node=transit_node,
                best_location=prop.best_location,
                postcode_node=prop.postcode,
                has_car=p_info.has_car,
                max_walk=p_info.bus_walk_penalty_minutes,
            )

            petrol_cost = PetrolCostAugmentNode(
                f"{prop.rid}/{key}/petrol_cost",
                commute_node=park_and_ride,
                financial_source=prop._svc.financial_source,
            )

            # Separate PetrolCostAugmentNode for the raw drive path so
            # drive-only commutes (where transit is unavailable) always
            # have fuel cost.
            drive_fuel = PetrolCostAugmentNode(
                f"{prop.rid}/{key}/drive_fuel",
                commute_node=drive_node,
                financial_source=prop._svc.financial_source,
            )
 

            walk_check = WalkLegCheckNode(
                f"{prop.rid}/{key}/walk_check",
                transit_node=transit_node,
                max_walk=p_info.bus_walk_penalty_minutes,
            )

            bus_route = BusRouteNode(
                f"{prop.rid}/{key}/bus_route",
                best_location=prop.best_location,
                walk_leg_check_node=walk_check,
                transit_node=transit_node,
            )

            bods_fare = BodsFareNode(
                f"{prop.rid}/{key}/bods_fare",
                bus_route_node=bus_route,
            )

            bus_augment = BusLegAugmentNode(
                f"{prop.rid}/{key}/bus_augment",
                transit_node=transit_node,
                walk_leg_check_node=walk_check,
                bus_route_node=bus_route,
                bods_fare_node=bods_fare,
            )
            prop._bus_augment_nodes.append(bus_augment)

            # Wrap bus_augment in IfThenElseNode — only active when walk is too long
            bus_if = IfThenElseNode(
                f"{prop.rid}/{key}/bus_if",
                Commute | None,
                condition_sources=(walk_check,),
                condition_fn=_bus_condition,
                then_branch=bus_augment,
            )

            # Create a RailFareNode for non-child commutes to apply NR fares
            rail_fare_node = None
            if not is_child:
                from houses.nodes.rail_fare_node import RailFareNode

                rail_fare_node = RailFareNode(
                    f"{prop.rid}/{key}/rail_fare",
                    transit_result=transit_node,
                    best_location=prop.best_location,
                )

            # Wrap rail_fare in IfThenElseNode — only active when NR fare is needed
            if is_child:
                # Children don't get NR fares — dummy IfThenElse that always returns None
                _dummy = commute_input_node(f"{prop.rid}/{key}/rail_fare_dummy")
                rail_fare_result = IfThenElseNode(
                    f"{prop.rid}/{key}/rail_fare_noop",
                    Commute | None,
                    condition_sources=(),
                    condition_fn=lambda: False,
                    then_branch=_dummy,
                )
            else:
                rail_fare_result = IfThenElseNode(
                    f"{prop.rid}/{key}/rail_fare_if",
                    Commute | None,
                    condition_sources=(transit_node,),
                    condition_fn=_needs_rail_fare,
                    then_branch=rail_fare_node,
                )

            selector = CommuteSelectorNode(
                f"{prop.rid}/{key}/commute",
                origin=prop.best_location,
                poi=poi_src,
                drive_result=drive_fuel,
                walk_result=walk_node,
                transit_result=petrol_cost,
                bus_result=bus_if,
                rail_fare_result=rail_fare_result,
                is_child=is_child,
                max_walk=p_info.bus_walk_penalty_minutes,
            )
            prop.commute_selectors[key] = selector

    prop.commute_breakdown = CommuteBreakdownNode(
        f"{prop.rid}/commute_breakdown",
        commute_selectors=prop.commute_selectors,
        persons_source=prop._svc.persons_source,
    )
