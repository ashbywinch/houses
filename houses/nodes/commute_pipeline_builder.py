from __future__ import annotations

from dag.if_then_else import IfThenElseNode
from dag.user_input_node import UserInputNode
from houses.model.domain import Commute, effective_acceptable_modes
from houses.nodes.bus import BodsFareNode, BusLegAugmentNode, BusRouteNode
from houses.nodes.commute import CommuteSelectorNode, MergeRailFareNode, _needs_rail_fare
from houses.nodes.commute_breakdown_node import CommuteBreakdownNode
from houses.nodes.park_and_ride import ParkAndRideAugmentNode
from houses.nodes.petrol import PersonPetrolMpgNode, PetrolCostAugmentNode
from houses.nodes.schools import SchoolLocationNode
from houses.nodes.transit import DriveNode, PersonMaxWalkNode, TflTransitNode, TransitNode, WalkNode
from houses.routing import CommuteRouter

_router = CommuteRouter()


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
            postcode = poi.address
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
                max_walk=int(p_info.bus_walk_penalty.magnitude),
            )
            # The walking TOLERANCE is a node read (like MPG): the gate
            # nodes depend on it so a settings/what-if change re-scores
            # the planned routes — the route-planning nodes (WalkNode,
            # TflTransitNode) deliberately do NOT, so no re-planning.
            max_walk_node = PersonMaxWalkNode(
                f"{prop.rid}/{key}/max_walk",
                persons_source=prop._svc.persons_source,
                person_name=p_info.name,
            )

            # Only create a DriveNode for persons who have a car.
            # If the destination is in the congestion zone, omit drive too
            # (handled below by checking drive_result=None).
            if p_info.has_car:
                drive_node = DriveNode(
                    f"{prop.rid}/{key}/drive",
                    best_location=prop.best_location,
                    poi=poi_src,
                    has_car=True,
                )
            else:
                drive_node = None
            no_bus_node = TflTransitNode(
                f"{prop.rid}/{key}/tfl_no_bus",
                best_location=prop.best_location,
                poi=poi_src,
                has_car=p_info.has_car,
                allow_bus=False,
            )
            with_bus_node = TflTransitNode(
                f"{prop.rid}/{key}/tfl_with_bus",
                best_location=prop.best_location,
                poi=poi_src,
                has_car=p_info.has_car,
                allow_bus=True,
            )
            transit_node = TransitNode(
                f"{prop.rid}/{key}/computed_transit",
                best_location=prop.best_location,
                poi=poi_src,
                has_car=p_info.has_car,
                no_bus_node=no_bus_node,
                with_bus_node=with_bus_node,
            )
            prop._transit_nodes.append(transit_node)

            park_and_ride = ParkAndRideAugmentNode(
                f"{prop.rid}/{key}/park_and_ride",
                transit_node=transit_node,
                best_location=prop.best_location,
                postcode_node=prop.postcode,
                has_car=p_info.has_car,
                max_walk_node=max_walk_node,
            )

            bus_route_node = BusRouteNode(
                f"{prop.rid}/{key}/bus_route",
                best_location=prop.best_location,
                poi=poi_src,
                _google_routes_post=_router.google_routes_post,
            )

            bods_fare_node = BodsFareNode(
                f"{prop.rid}/{key}/bods_fare",
                bus_route_node=bus_route_node,
            )

            bus_augment = BusLegAugmentNode(
                f"{prop.rid}/{key}/bus_augment",
                transit_input=park_and_ride,
                bus_route_node=bus_route_node,
                bods_fare_node=bods_fare_node,
                max_walk_node=max_walk_node,
            )

            # Omit drive entirely when the destination is in the London
            # congestion zone — no point computing a route that can't exist.
            dest_addr = getattr(poi, "address", str(poi))
            in_zone = bool(dest_addr) and CommuteRouter.in_congestion_zone(dest_addr)

            selector = CommuteSelectorNode(
                f"{prop.rid}/{key}/commute",
                origin=prop.best_location,
                poi=poi_src,
                walk_result=walk_node,
                transit_result=bus_augment,
                drive_result=None if in_zone else drive_node,
                is_child=is_child,
                max_walk_node=max_walk_node,
                acceptable_modes=effective_acceptable_modes(poi),
            )

            if is_child:
                # Children don't get NR fares — dummy IfThenElse that always returns None
                _dummy = UserInputNode[str](f"{prop.rid}/{key}/rail_fare_dummy", str)
                rail_fare_result = IfThenElseNode(
                    f"{prop.rid}/{key}/rail_fare_noop",
                    Commute | None,
                    condition_sources=(),
                    condition_fn=lambda: False,
                    then_branch=_dummy,
                )
            else:
                from houses.nodes.rail_fare_node import RailFareNode

                # The fare node knows the SELECTED commute: when the choice
                # is drive/walk it passes the transit commute through
                # without running the NR lookup, so an unchosen route's
                # fare can't fail noisily.
                rail_fare_node = RailFareNode(
                    f"{prop.rid}/{key}/rail_fare",
                    transit_result=transit_node,
                    best_location=prop.best_location,
                    selector=selector,
                )
                # The fare branch activates only when the SELECTED commute
                # uses transit legs and needs an NR fare — gate on the
                # selector's choice so a drive/walk selection never
                # activates the fare node.  The merge additionally treats
                # the fare as a conditional dependency.
                rail_fare_result = IfThenElseNode(
                    f"{prop.rid}/{key}/rail_fare_if",
                    Commute | None,
                    condition_sources=(transit_node, selector),
                    condition_fn=_needs_rail_fare,
                    then_branch=rail_fare_node,
                )

            merge_node = MergeRailFareNode(
                f"{prop.rid}/{key}/merge",
                commute_result=selector,
                rail_fare_result=rail_fare_result,
            )

            mpg_node = PersonPetrolMpgNode(
                f"{prop.rid}/{key}/petrol_mpg",
                persons_source=prop._svc.persons_source,
                person_name=p_info.name,
            )
            final_fuel = PetrolCostAugmentNode(
                f"{prop.rid}/{key}/final_fuel",
                commute_node=merge_node,
                petrol_mpg_node=mpg_node,
                petrol_cost_per_litre_node=prop._svc.setting_nodes.get("settings/petrol_cost_per_litre"),
            )
            prop.commute_selectors[key] = final_fuel

    prop.commute_breakdown = CommuteBreakdownNode(
        f"{prop.rid}/commute_breakdown",
        commute_selectors=prop.commute_selectors,
        persons_source=prop._svc.persons_source,
    )
