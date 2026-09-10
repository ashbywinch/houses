from __future__ import annotations

from dag.if_then_else_node import IfThenElseNode, IfThenElseOptions
from dag.user_input_node import UserInputNode
from houses.model.domain import Commute, effective_acceptable_modes
from houses.nodes.bus import BodsFareNode, BusLegAugmentNode, BusRouteNode
from houses.nodes.commute import CommuteSelectorNode, CommuteSelectorOptions, MergeRailFareNode, needs_rail_fare
from houses.nodes.park_and_ride_augment_node import ParkAndRideAugmentNode, ParkAndRideOptions
from houses.nodes.petrol import PersonPetrolMpgNode, PetrolCostAugmentNode
from houses.nodes.rail_fare_node import RailFareNode
from houses.nodes.schools import SchoolLocationNode
from houses.nodes.transit import (
    DestinationPlaceNode,
    DriveNode,
    PersonMaxWalkNode,
    RouteOptions,
    TflTransitNode,
    TransitNode,
    TransitOptions,
    WalkNode,
)
from houses.services_provider import get_services


def _commute_router():
    """The routing aggregate, read from the DI container (lazy)."""

    return get_services().commute_router


def build_commute_pipeline(prop, keys: set[str] | None = None) -> None:
    """Build the commute pipelines for *keys* — default: every person/POI
    pair in the current persons source.

    Nodes are added into ``prop.commute_selectors`` IN PLACE — the dict
    and the breakdown node are owned by ``PropertyNodes`` and are never
    replaced here: the breakdown holds the selectors dict by reference
    and the household-cost node wired itself to the breakdown object at
    construction. Recreating either orphans the other's wiring (the live
    0.0-commutes regression). A settings change adds or removes
    pipelines; nothing is ever rebuilt.
    """

    for p_info in prop._svc.persons_source._value or []:
        p_name = p_info.name
        pois = p_info.places_of_interest
        for poi in pois:
            label = poi.label
            key = f"{p_name}/{label}"
            if keys is not None and key not in keys:
                continue

            is_child = p_info.is_child
            # The destination's CURRENT PlaceOfInterest, read live from
            # the persons source: routes re-plan and the congestion-zone
            # gate re-evaluates when the address or trips change — no
            # rebuild, no rewiring, nothing frozen.  For children the
            # school node resolves the school's real location instead
            # (the POI carries no address for schools).
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
                place = poi_src
            else:
                place = DestinationPlaceNode(
                    f"{prop.rid}/{key}/place",
                    persons_source=prop._svc.persons_source,
                    person_name=p_name,
                    label=label,
                )

            walk_node = WalkNode(
                f"{prop.rid}/{key}/walk",
                options=RouteOptions(
                    best_location=prop.best_location,
                    poi=place,
                    max_walk=int(p_info.bus_walk_penalty.magnitude),
                ),
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

            # Only create a DriveNode for persons who have a car. The
            # congestion-charge rule is enforced inside DriveNode against
            # the destination's current address.
            if p_info.has_car:
                drive_node = DriveNode(
                    f"{prop.rid}/{key}/drive",
                    options=RouteOptions(
                        best_location=prop.best_location,
                        poi=place,
                        has_car=True,
                        ),
                )
            else:
                drive_node = None
            no_bus_node = TflTransitNode(
                f"{prop.rid}/{key}/tfl_no_bus",
                options=TransitOptions(
                    best_location=prop.best_location,
                    poi=place,
                    has_car=p_info.has_car,
                    allow_bus=False,
                ),
            )
            with_bus_node = TflTransitNode(
                f"{prop.rid}/{key}/tfl_with_bus",
                options=TransitOptions(
                    best_location=prop.best_location,
                    poi=place,
                    has_car=p_info.has_car,
                    allow_bus=True,
                ),
            )
            transit_node = TransitNode(
                f"{prop.rid}/{key}/computed_transit",
                options=TransitOptions(
                    best_location=prop.best_location,
                    poi=place,
                    has_car=p_info.has_car,
                    no_bus_node=no_bus_node,
                    with_bus_node=with_bus_node,
                    # National Rail fallback: TfL's planner has no
                    # coverage west of Newbury (proven: Hungerford 404s
                    # from every origin form) — route those journeys via
                    # Google Routes TRANSIT and price them downstream.
                    transit_route_fn=_commute_router().transit_route,
                ),
            )
            park_and_ride = ParkAndRideAugmentNode(
                f"{prop.rid}/{key}/park_and_ride",
                options=ParkAndRideOptions(
                    transit_node=transit_node,
                    best_location=prop.best_location,
                    postcode_node=prop.postcode,
                    has_car=p_info.has_car,
                    max_walk_node=max_walk_node,
                ),
            )

            bus_route_node = BusRouteNode(
                f"{prop.rid}/{key}/bus_route",
                best_location=prop.best_location,
                poi=place,
                _google_routes_post=_commute_router().google_routes_post,
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

            selector = CommuteSelectorNode(
                f"{prop.rid}/{key}/commute",
                options=CommuteSelectorOptions(
                    origin=prop.best_location,
                    poi=place,
                    walk_result=walk_node,
                    transit_result=bus_augment,
                    drive_result=drive_node,
                    is_child=is_child,
                    max_walk_node=max_walk_node,
                    acceptable_modes=effective_acceptable_modes(poi),
                ),
            )

            if is_child:
                # Children don't get NR fares — dummy IfThenElse that always returns None
                _dummy = UserInputNode[str](f"{prop.rid}/{key}/rail_fare_dummy", str)
                rail_fare_result = IfThenElseNode(
                    f"{prop.rid}/{key}/rail_fare_noop",
                    Commute | None,
                    options=IfThenElseOptions(
                        condition_sources=(),
                        condition_fn=lambda: False,
                        then_branch=_dummy,
                    ),
                )
            else:
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
                    options=IfThenElseOptions(
                        condition_sources=(transit_node, selector),
                        condition_fn=needs_rail_fare,
                        then_branch=rail_fare_node,
                    ),
                )

            merge_node = MergeRailFareNode(
                f"{prop.rid}/{key}/merge",
                commute_result=selector,
                rail_fare_result=rail_fare_result,
            )

            # Geocodes the CURRENT destination address; the drive node
            # reads it to enforce the congestion-charge rule as addresses
            # change (no build-time mode decisions left in the builder).
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
                is_child=is_child,
            )
            # The entry is what the user reads: name it in the domain's
            # words, not after the step that happens to run last.
            final_fuel.display_name = f"{label} commute"
            # The pipeline IS the entry every consumer reads.  No wrapper:
            # wrapping a failure into a plausible empty value would drop the
            # person's cost out of the household total silently, and hide the
            # failing step from provenance and from the code-change sweep.
            # A commute that cannot be computed propagates as an error so it
            # is visible and fixable (2026-09-10).
            prop.commute_selectors[key] = final_fuel
