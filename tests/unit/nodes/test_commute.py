from __future__ import annotations

import pytest
from money import Money
from pint import Quantity

import dag.user_input_node  # noqa: F401 — register Money/Quantity pydantic schemas
from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.if_then_else import IfThenElseNode
from dag.node import Node
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from tests.helpers import FixedCommuteNode


def _succeeded_walk_check(val: bool = False) -> DerivedNode:
    """Build a minimal walk-check node whose ``_attempt`` is already resolved."""
    from houses.nodes.transit import WalkLegCheckNode

    t = UserInputNode[dict]("_wc_t", dict)
    w = WalkLegCheckNode("_wc", transit_node=t)
    w._attempt = Attempt.succeeded(val)
    return w


class TestCommuteSelectorNode:
    @pytest.mark.asyncio
    async def test_transit_takes_priority(self):
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = FixedCommuteNode("transit")
        bus = FixedCommuteNode("bus")
        walk = FixedCommuteNode("walk")
        drive = FixedCommuteNode("drive")
        _succeeded_walk_check(False)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            walk_result=walk,
            drive_result=drive,
            max_walk=30,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi)

        transit_commute = _make_commute(duration_min=32, cost_gbp=4.50)
        bus_commute = _make_commute(duration_min=55, cost_gbp=2.00)
        walk_commute = _make_commute(duration_min=60, cost_gbp=0)
        drive_commute = _make_commute(duration_min=45, cost_gbp=8.00)

        transit.push(transit_commute)
        bus.push(bus_commute)
        walk.push(walk_commute)
        drive.push(drive_commute)

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == transit_commute

    @pytest.mark.asyncio
    async def test_fallback_to_bus(self):
        """When transit is pending, selector is pending."""
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = FixedCommuteNode("transit")

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            max_walk=30,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi)

        await flush_processor()

        a = await node.attempt()
        assert a.pending, "Selector should be pending when transit hasn't been set"

    @pytest.mark.asyncio
    async def test_impossible_when_both_fail(self):
        """When all routes fail (impossible), selector is impossible."""
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = _impossible_commute("transit")

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            walk_result=_impossible_commute("walk"),
            drive_result=_impossible_commute("drive"),
            max_walk=30,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi)

        await flush_processor()

        a = await node.attempt()
        assert a.impossible, f"Should be impossible when all routes fail, got {a.status}: {a.error}"

    @pytest.mark.asyncio
    async def test_impossible_when_origin_missing(self):
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = FixedCommuteNode("transit")
        FixedCommuteNode("bus")
        _succeeded_walk_check(False)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            max_walk=30,
        )

        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi)

        await flush_processor()

        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_recomputes_when_transit_updates(self):
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = FixedCommuteNode("transit")
        bus = FixedCommuteNode("bus")
        walk = FixedCommuteNode("walk")
        drive = FixedCommuteNode("drive")
        _succeeded_walk_check(False)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            walk_result=walk,
            drive_result=drive,
            max_walk=30,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        office_poi = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office_poi)
        bus.push(_make_commute(duration_min=55, cost_gbp=2.00), "Bus")
        walk.push(_make_commute(duration_min=60, cost_gbp=0), "test")
        drive.push(_make_commute(duration_min=45, cost_gbp=8.00), "test")
        transit.push(_make_commute(duration_min=32, cost_gbp=4.50), "TfL")

        await flush_processor()

        assert (await node.attempt()).value_or_none().daily_cost == Money("4.50", "GBP")

        transit.push(_make_commute(duration_min=30, cost_gbp=3.00), "TfL-Updated")

        await flush_processor()
        assert (await node.attempt()).value_or_none().daily_cost == Money("3.00", "GBP")

    @pytest.mark.asyncio
    async def test_to_json_shape(self):
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = FixedCommuteNode("transit")
        bus = FixedCommuteNode("bus")
        walk = FixedCommuteNode("walk")
        drive = FixedCommuteNode("drive")
        _succeeded_walk_check(False)

        node = CommuteSelectorNode(
            "commute_selector",
            origin=origin,
            poi=poi,
            transit_result=transit,
            walk_result=walk,
            drive_result=drive,
            max_walk=30,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
        transit.push(_make_commute(duration_min=32, cost_gbp=4.50), "TfL")
        bus.push(_make_commute(duration_min=55, cost_gbp=2.00), "Bus")
        walk.push(_make_commute(duration_min=60, cost_gbp=0), "test")
        drive.push(_make_commute(duration_min=45, cost_gbp=8.00), "test")

        await flush_processor()

        j = await node.to_json()
        assert j["status"] == "succeeded"
        assert j["value"] is not None
        assert "error" not in j
        assert j.get("is_child") is False, "Default is_child should be False"

    @pytest.mark.asyncio
    async def test_is_child_flag_in_json(self):
        """CommuteSelectorNode with is_child=True must propagate the flag
        into both the outer wrapper AND the inner Commute value.

        A bug in ComputedTransitNode hardcodes is_child=False on the Commute
        object, so the selector must overwrite it after selection.
        """
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("origin", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)
        transit = FixedCommuteNode("transit")
        bus = FixedCommuteNode("bus")
        _succeeded_walk_check(False)

        node = CommuteSelectorNode(
            "commute_selector_child",
            origin=origin,
            poi=poi,
            transit_result=transit,
            is_child=True,
            max_walk=30,
        )

        origin.push(GeoPoint(51.5, -0.1), "user")
        poi.push(PlaceOfInterest("School", "SW1V 2QQ"), "config")
        transit.push(_make_commute(duration_min=10, cost_gbp=0), "walk")
        bus.push(None)

        await flush_processor()

        j = await node.to_json()
        assert j["status"] == "succeeded"
        # Outer wrapper must propagate is_child
        assert j.get("is_child") is True, f"outer is_child should be True, got {j.get('is_child')}"
        # Inner Commute value must also carry is_child=True so the
        # frontend's schoolWalkMin() can identify it as a school commute.
        val = j.get("value")
        assert val is not None
        assert val.get("is_child") is True, (
            f"value.is_child should be True for child commutes, "
            f"got {val.get('is_child')}. "
            f"The CommuteSelectorNode must override the transit node's "
            f"hardcoded is_child=False."
        )


class TestMergeRailFareNode:
    """MergeRailFareNode — applies NR fare to transit CostGroup."""

    @pytest.mark.asyncio
    async def test_passes_through_when_no_rail_fare(self):
        """When rail_fare is pending, MergeRailFareNode blocks."""
        from houses.nodes.commute import MergeRailFareNode

        commute_src = FixedCommuteNode("merge_c")
        rail_fare_src = FixedCommuteNode("merge_rf")  # never pushed → pending

        node = MergeRailFareNode(
            "merge_test",
            commute_result=commute_src,
            rail_fare_result=rail_fare_src,
        )

        commute_src.push(_make_commute(duration_min=32, cost_gbp=4.50))

        await flush_processor()

        a = await node.attempt()
        assert a.pending, "Should block when rail_fare is pending"

    @pytest.mark.asyncio
    async def test_passes_through_when_rail_fare_has_no_cost(self):
        """When rail_fare has no cost (None), the commute passes through unchanged."""
        from houses.nodes.commute import MergeRailFareNode

        commute_src = FixedCommuteNode("merge_c2")
        rail_fare_src = FixedCommuteNode("merge_rf2")

        node = MergeRailFareNode(
            "merge_test2",
            commute_result=commute_src,
            rail_fare_result=rail_fare_src,
        )

        commute_src.push(_make_commute(duration_min=32, cost_gbp=0))
        rail_fare_src.push(_make_commute(duration_min=30, cost_gbp=0))

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, "Should succeed with zero-cost rail_fare"
        val = a.value_or_none()
        assert val is not None
        # Cost should remain 0 (rail_fare has no cost to add)
        assert float(val.daily_cost.amount) == 0

    @pytest.mark.asyncio
    async def test_applies_rail_fare_to_transit_commute(self):
        """Rail_fare cost replaces the transit CostGroup cost when provided."""
        from houses.nodes.commute import MergeRailFareNode

        commute_src = FixedCommuteNode("merge_c3")
        rail_fare_src = FixedCommuteNode("merge_rf3")

        node = MergeRailFareNode(
            "merge_test3",
            commute_result=commute_src,
            rail_fare_result=rail_fare_src,
        )

        # Transit commute with £0 cost and train leg
        commute_src.push(_make_commute(duration_min=32, cost_gbp=0))
        # Rail fare with £41 cost
        rail_fare_src.push(_make_commute(duration_min=30, cost_gbp=41.0))

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, "Should apply rail_fare cost"
        val = a.value_or_none()
        assert val is not None
        assert float(val.daily_cost.amount) == 41.0, f"Expected £41.0, got £{val.daily_cost.amount}"

    @pytest.mark.asyncio
    async def test_combines_existing_cost_with_rail_fare(self):
        """When transit has some cost (e.g. parking) and unpriced transit legs,
        the rail_fare cost is added to the existing cost."""
        from pint import Quantity

        from houses.nodes.commute import MergeRailFareNode

        commute_src = FixedCommuteNode("merge_c4")
        rail_fare_src = FixedCommuteNode("merge_rf4")

        node = MergeRailFareNode(
            "merge_test4",
            commute_result=commute_src,
            rail_fare_result=rail_fare_src,
        )

        office = PlaceOfInterest("Office", "SW1V 2QQ")
        person = Person("Simon", True, places_of_interest=(office,))
        train_leg = JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(30, "minute"), end_station="London Waterloo")
        park_leg = JourneyLeg(mode=LegMode.PARK, duration=Quantity(0, "minute"))
        transit_commute = Commute(
            person=person,
            label="Office",
            destination=office,
            duration=Quantity(60, "minute"),  # type: ignore[arg-type]

            daily_cost=Money("10.90", "GBP"),  # parking cost only
            mode="transit",
            _details=(
                CostGroup(legs=(train_leg,), operator="", cost=None),  # unpriced transit!
                CostGroup(legs=(park_leg,), operator="Ascot Car Park", cost=Money("10.90", "GBP")),
            ),
        )
        commute_src.push(transit_commute)
        rail_fare_src.push(_make_commute(duration_min=30, cost_gbp=41.0))

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, "Should apply rail_fare cost"
        val = a.value_or_none()
        assert val is not None
        # Should include both parking (£10.90) and rail_fare (£41.00)
        assert float(val.daily_cost.amount) == 51.90, f"Expected £51.90, got £{val.daily_cost.amount}"

    @pytest.mark.asyncio
    async def test_passes_through_when_commute_has_no_transit_legs(self):
        """When the selected commute is walk or drive (no transit legs),
        MergeRailFareNode passes through without merging."""
        from houses.nodes.commute import MergeRailFareNode

        commute_src = FixedCommuteNode("merge_c5")
        rail_fare_src = FixedCommuteNode("merge_rf5")

        node = MergeRailFareNode(
            "merge_test5",
            commute_result=commute_src,
            rail_fare_result=rail_fare_src,
        )

        # Walk commute (no transit legs), £0 cost
        walk_commute = _make_commute(duration_min=20, cost_gbp=0)
        # Replace train leg with walk leg
        from pint import Quantity

        walk_commute = Commute(
            person=walk_commute.person,
            label=walk_commute.label,
            destination=walk_commute.destination,
            duration=Quantity(20, "minute"),  # type: ignore[arg-type]

            daily_cost=Money("0", "GBP"),
            mode="walk",
            _details=(
                CostGroup(
                    legs=(JourneyLeg(mode=LegMode.WALK, duration=Quantity(20, "minute")),),
                    operator="",
                    cost=Money("0", "GBP"),
                ),
            ),
        )
        commute_src.push(walk_commute)
        rail_fare_src.push(_make_commute(duration_min=30, cost_gbp=41.0))

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        # Walk cost should remain £0 (no transit legs to replace)
        assert float(val.daily_cost.amount) == 0, f"Expected £0, got £{val.daily_cost.amount}"


class TestWalkLegCheckNode:
    """Direct WalkLegCheckNode tests."""

    @pytest.mark.asyncio
    async def test_walk_less_than_max(self):
        from houses.commute import CostGroup
        from houses.nodes.transit import WalkLegCheckNode

        walk_leg = JourneyLeg(mode=LegMode.WALK, duration=Quantity(10, "minute"))
        transit_commute = _make_commute(duration_min=32, cost_gbp=4.50)
        transit_commute = Commute(
            person=transit_commute.person,
            label=transit_commute.label,
            destination=transit_commute.destination,
            duration=transit_commute.duration,
            daily_cost=transit_commute.daily_cost,
            mode=transit_commute.mode,
            _details=(CostGroup(legs=(walk_leg,), operator="", cost=None),),
        )
        transit = UserInputNode[Commute]("transit_wl", Commute)
        node = WalkLegCheckNode("walk_check_wl", transit_node=transit, max_walk=30)
        transit.push(transit_commute)
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value is False

    @pytest.mark.asyncio
    async def test_walk_exceeds_max(self):
        from houses.commute import CostGroup
        from houses.nodes.transit import WalkLegCheckNode

        walk_leg = JourneyLeg(mode=LegMode.WALK, duration=Quantity(45, "minute"))
        transit_commute = _make_commute(duration_min=32, cost_gbp=4.50)
        transit_commute = Commute(
            person=transit_commute.person,
            label=transit_commute.label,
            destination=transit_commute.destination,
            duration=transit_commute.duration,
            daily_cost=transit_commute.daily_cost,
            mode=transit_commute.mode,
            _details=(CostGroup(legs=(walk_leg,), operator="", cost=None),),
        )
        transit = UserInputNode[Commute]("transit_we", Commute)
        node = WalkLegCheckNode("walk_check_we", transit_node=transit, max_walk=30)
        transit.push(transit_commute)
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value is True


# ── helpers ────────────────────────────────────────────────────────────────


def _make_person(bus_walk_penalty: int = 30, name: str = "Simon"):
    """Create a minimal Person-like object with the attributes WalkLegCheckNode reads."""
    office = PlaceOfInterest("Office", "SW1V 2QQ")
    return Person(name, True, places_of_interest=(office,), bus_walk_penalty=Quantity(bus_walk_penalty, "minute"))


def _make_commute(duration_min=32, cost_gbp=4.50):
    from pint import Quantity

    from houses.commute import LegMode

    office = PlaceOfInterest("Office", "SW1V 2QQ")
    person = Person("Simon", True, places_of_interest=(office,))
    leg = JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(duration_min, "minute"), end_station="London Paddington")
    return Commute(
        person=person,
        label=office.label,
        destination=office,
        duration=Quantity(duration_min, "minute"),  # type: ignore[arg-type]

        daily_cost=Money(str(cost_gbp), "GBP"),
        _details=(CostGroup(legs=(leg,), operator="TfL", cost=Money(str(cost_gbp), "GBP")),),
    )


def _drive_commute(duration_min=16, cost_gbp=5.0) -> Commute:
    """A real driving commute — no transit legs, so NR fares never apply."""
    office = PlaceOfInterest("Office", "SW1V 2QQ")
    person = Person("Simon", True, places_of_interest=(office,))
    leg = JourneyLeg(mode=LegMode.DRIVE, duration=Quantity(duration_min, "minute"))  # type: ignore[arg-type]
    return Commute(
        person=person,
        label=office.label,
        destination=office,
        duration=Quantity(duration_min, "minute"),  # type: ignore[arg-type]

        daily_cost=Money(str(cost_gbp), "GBP"),
        mode="drive",
        _details=(CostGroup(legs=(leg,), cost=Money(str(cost_gbp), "GBP")),),
    )


def _bus_condition(walk_check):
    return walk_check.succeeded and bool(walk_check.value)


def _bus_if(walk_check: DerivedNode, bus_node: Node) -> IfThenElseNode:
    """Wrap a bus node in IfThenElseNode activated when walk check is True."""
    return IfThenElseNode(
        f"_bus_if_{id(bus_node)}",
        Commute,
        condition_sources=(walk_check,),
        condition_fn=_bus_condition,
        then_branch=bus_node,
    )


def _rail_fare_if(transit_node: Node, rail_fare_node: Node) -> IfThenElseNode:
    """Wrap a rail_fare node in IfThenElseNode activated when NR fare is needed."""
    from houses.nodes.commute import _needs_rail_fare

    return IfThenElseNode(
        f"_rf_if_{id(rail_fare_node)}",
        Commute,
        condition_sources=(transit_node,),
        condition_fn=_needs_rail_fare,
        then_branch=rail_fare_node,
    )


class _ImpossibleCommuteNode(DerivedNode[Commute]):
    """A node that always returns Attempt.impossible for Commute."""

    def __init__(self, node_id: str = "_impossible"):
        super().__init__(node_id, Commute, ())

    def compute(self):
        return Attempt.impossible("not available")


def _impossible_commute(name: str = "walk") -> DerivedNode[Commute]:
    """Return a DerivedNode that always returns Attempt.impossible for Commute."""
    return _ImpossibleCommuteNode(f"_impl_{name}")


@pytest.mark.asyncio
async def test_commute_selector_init_with_persisted_result():
    """Constructing a CommuteSelectorNode that loads a persisted result
    must not crash when _is_stale() calls _get_active_deps() before
    the subclass has set its named attributes."""
    from pydantic import TypeAdapter

    from dag.persistence import save_node_result
    from houses.model.domain import Commute as CommuteDomain
    from houses.nodes.commute import CommuteSelectorNode

    node_id = "test_init_crash_persisted"

    # Persist a valid Commute dict so the node can load it with TypeAdapter(Commute)
    commute = _make_commute(duration_min=5, cost_gbp=5.0)
    value_dict = TypeAdapter(CommuteDomain).dump_python(commute, mode="json")
    save_node_result(
        node_id,
        {
            "status": "succeeded",
            "value": value_dict,
        },
    )

    origin = UserInputNode[GeoPoint]("origin_crash", GeoPoint)
    poi = UserInputNode[PlaceOfInterest]("poi_crash", PlaceOfInterest)
    transit = UserInputNode[dict]("transit_crash", dict)
    UserInputNode[dict]("bus_crash", dict)
    _succeeded_walk_check(False)

    node = CommuteSelectorNode(
        node_id,
        origin=origin,
        poi=poi,
        transit_result=transit,
        walk_result=_impossible_commute("walk"),
        drive_result=_impossible_commute("drive"),
        max_walk=30,
    )
    node.disconnect()


class TestFareBetween:
    """RailFareRegistry.fare_between — exact pair lookup."""

    def test_fare_between_exact_match(self):
        """Direct station pair returns the fare."""
        from houses.rail_fares import RailFareRegistry
        from houses.stations import Station

        reg = RailFareRegistry()
        reg._fares_by_pair = {
            frozenset({"MAI", "PAD"}): Money(16.40, "GBP"),
        }

        maidenhead = Station("Maidenhead", "MAI", GeoPoint(51.52, -0.72))
        paddington = Station("London Paddington", "PAD", GeoPoint(51.52, -0.18))

        fare = reg.fare_between(maidenhead, paddington)
        assert fare is not None
        assert float(fare.amount) == 16.40

    def test_fare_between_no_match(self):
        """Returns None when no pair matches (no LON fallback either)."""
        from houses.rail_fares import RailFareRegistry
        from houses.stations import Station

        reg = RailFareRegistry()
        reg._fares_by_pair = {
            frozenset({"MAI", "LON"}): Money(15.00, "GBP"),
        }

        # ABW (Aberystwyth) has no LON entry, so no fallback match
        aber = Station("Aberystwyth", "ABW", GeoPoint(52.41, -4.08))
        oxford = Station("Oxford", "OXF", GeoPoint(51.75, -1.26))

        fare = reg.fare_between(aber, oxford)
        assert fare is None

    def test_fare_between_reverse_match(self):
        """Fares are symmetric — reverse pair also matches."""
        from houses.rail_fares import RailFareRegistry
        from houses.stations import Station

        reg = RailFareRegistry()
        reg._fares_by_pair = {
            frozenset({"PAD", "MAI"}): Money(16.40, "GBP"),
        }

        maidenhead = Station("Maidenhead", "MAI", GeoPoint(51.52, -0.72))
        paddington = Station("London Paddington", "PAD", GeoPoint(51.52, -0.18))

        fare = reg.fare_between(maidenhead, paddington)
        assert fare is not None
        assert float(fare.amount) == 16.40


class TestDerivedNodeProvenance:
    """DerivedNode.build_provenance uses last path segment as label."""

    @pytest.mark.asyncio
    async def test_provenance_label_uses_last_path_segment(self):
        from dag.derived_node import DerivedNode
        from dag.user_input_node import UserInputNode

        dep = UserInputNode[float]("dep", float)
        dep.push(42.0, "test")
        await flush_processor()

        class TestNode(DerivedNode[float]):
            def compute(self, val):
                return val

            def to_json(self):
                return {"status": "succeeded", "value": self._attempt.value_or_none()}

        node = TestNode("rid/person/poi/test_node", float, (dep,))
        await flush_processor()

        prov = await node.build_provenance()
        assert prov.label == "Test Node", f"Expected 'Test Node', got '{prov.label}'"
        assert "dep" in prov.sources


class TestRailFareNode:
    """RailFareNode — fare enrichment, pass-through, and missing-dependency behavior."""

    @pytest.mark.asyncio
    async def test_pending_when_no_transit(self):
        """Node stays pending when transit has no result yet."""
        from houses.nodes.rail_fare_node import RailFareNode

        transit = UserInputNode[Commute]("rf_pend", Commute)
        location = UserInputNode[GeoPoint]("rf_pend_loc", GeoPoint)
        # transit NOT pushed — node should remain pending

        node = RailFareNode("rf_pend_test", transit_result=transit, best_location=location)
        await flush_processor()

        a = await node.attempt()
        assert a.pending, f"Expected pending, got {a.status}"

    @pytest.mark.asyncio
    async def test_passes_through_when_transit_has_cost(self):
        """When transit already has a cost (>0), node passes through without NR lookup."""
        from houses.nodes.rail_fare_node import RailFareNode

        transit = UserInputNode[Commute]("rf_skip", Commute)
        location = UserInputNode[GeoPoint]("rf_skip_loc", GeoPoint)

        transit.push(_make_commute(cost_gbp=5.0), "TfL")
        location.push(GeoPoint(51.5, -0.1), "test")

        node = RailFareNode("rf_skip_test", transit_result=transit, best_location=location)
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        assert val.daily_cost.amount == 5.0  # unchanged from transit, no NR lookup

    @pytest.mark.asyncio
    async def test_lookup_skipped_when_selection_is_drive(self):
        """A drive selection means the fare for the unchosen transit route
        is not applicable — the NR lookup must never run (no terminal
        station error), the transit commute passes through inertly."""
        from houses.nodes.rail_fare_node import RailFareNode

        class _FixedSel(DerivedNode[Commute]):
            def __init__(self, commute: Commute):
                super().__init__("rf_drive_sel", Commute, ())
                self._att = Attempt.succeeded(commute)

            async def attempt(self):
                return self._att

            def latest_attempt(self):
                return self._att

            def compute(self, *dep_attempts):
                raise AssertionError("fixed node should not compute")

        # Feasible transit with unpriced train legs ending at "Reading" —
        # the registry has no station there, so an actual lookup would
        # fail with "terminal station not found".
        office = PlaceOfInterest("Office", "SW1V 2QQ")
        person = Person("Simon", True, places_of_interest=(office,))
        leg = JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(120, "minute"), end_station="Reading")  # type: ignore[arg-type]
        transit_commute = Commute(
            person=person,
            label="Bracknell",
            destination=office,
            duration=Quantity(120, "minute"),  # type: ignore[arg-type]
            daily_cost=Money("0", "GBP"),
            _details=(CostGroup(legs=(leg,), operator="TfL", cost=None),),
        )
        transit = UserInputNode[Commute]("rf_dr_sel", Commute)
        location = UserInputNode[GeoPoint]("rf_dr_sel_loc", GeoPoint)
        transit.push(transit_commute, "TfL")
        location.push(GeoPoint(51.5, -0.1), "test")

        selected = _FixedSel(_drive_commute(duration_min=16, cost_gbp=5.0))
        node = RailFareNode(
            "rf_dr_sel_test", transit_result=transit, best_location=location, selector=selected
        )
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, f"drive selection must skip the fare lookup, got: {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        assert val.daily_cost.amount == 0  # transit commute passed through unpriced

    @pytest.mark.asyncio
    async def test_infeasible_transit_passes_through_without_crash(self):
        """An infeasible (no-route) transit commute must pass through — the
        fare node must never touch .details on an infeasible commute."""
        from houses.nodes.rail_fare_node import RailFareNode

        transit = UserInputNode[Commute]("rf_inf", Commute)
        location = UserInputNode[GeoPoint]("rf_inf_loc", GeoPoint)
        infeasible = Commute(
            person=Person(name="", has_car=False),
            label="NoRoute",
            destination=PlaceOfInterest(label="", address=""),
            duration=Quantity(0, "minute"),  # type: ignore[arg-type]
            daily_cost=Money("0", "GBP"),
            mode="transit",
            _details=(),
            infeasible=True,
        )
        transit.push(infeasible, "TfL")
        location.push(GeoPoint(51.5, -0.1), "test")

        node = RailFareNode("rf_inf_test", transit_result=transit, best_location=location)
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, f"infeasible transit must pass through, got: {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        assert val.infeasible

    @pytest.mark.asyncio
    async def test_enriches_commute_with_rail_fare(self, tmp_path):
        """Commute with zero daily cost and train leg gets NR fare added (17.00 + 2.80) × 2 → 39.60."""
        from unittest.mock import patch

        from pint import Quantity

        from houses.commute import LegMode
        from houses.nodes.rail_fare_node import RailFareNode
        from houses.rail_fares import RailFareRegistry
        from houses.services_provider import get_services
        from houses.stations import StationRegistry

        # Set up registry with stations and fare
        stations_csv = tmp_path / "stations.csv"
        stations_csv.write_text(
            "stationName,crsCode,lat,long\nWoking,WOK,51.317,-0.556\nFenchurch Street,FST,51.511,-0.079\n"
        )
        fares_csv = tmp_path / "fares.csv"
        fares_csv.write_text("origin_crs,dest_crs,single_fare_gbp\nWOK,FST,17.00\n")

        reg = RailFareRegistry(
            station_registry=StationRegistry(_stations_csv=stations_csv),
            _fares_csv=fares_csv,
        )
        get_services().rail_fare_registry = reg

        transit = UserInputNode[Commute]("rf_fare", Commute)
        location = UserInputNode[GeoPoint]("rf_fare_loc", GeoPoint)

        office = PlaceOfInterest("Office", "EC3A 7LP")
        person = Person("Lorena", True, places_of_interest=(office,))
        commute = Commute(
            person=person,
            label=office.label,
            destination=office,
            duration=Quantity(78, "minute"),  # type: ignore[arg-type]

            daily_cost=Money("0", "GBP"),
            _details=(
                CostGroup(
                    legs=(
                        JourneyLeg(
                            mode=LegMode.BUS,
                            duration=Quantity(10, "minute"),  # type: ignore[arg-type]

                            start_station="",
                            end_station="",
                            line_name="",
                        ),
                        JourneyLeg(
                            mode=LegMode.TRAIN,
                            duration=Quantity(30, "minute"),  # type: ignore[arg-type]

                            start_station="WOK",
                            end_station="Fenchurch Street",
                            line_name="Great Western Railway",
                        ),
                    ),
                    operator="TfL",
                    cost=Money("0", "GBP"),
                ),
            ),
        )

        transit.push(commute)
        location.push(GeoPoint(51.317, -0.556), "geocode")

        node = RailFareNode("rf_fare_test", transit_result=transit, best_location=location)

        with patch("houses.tfl_client.TflClient.get_tube_leg_fare", return_value=None):
            await flush_processor()

        a = await node.attempt()
        assert a.succeeded, f"Expected succeeded, got {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        # (17.00 + 2.80) × 2 = 39.60
        assert float(val.daily_cost.amount) == 39.60
        # The transit CostGroup must also have its cost attributed
        transit_cg = next((cg for cg in val.details if cg.operator == "TfL"), None)
        assert transit_cg is not None, "TfL CostGroup should exist"
        assert transit_cg.cost is not None, "TfL CostGroup should have cost attributed"
        assert float(transit_cg.cost.amount) == 39.60, f"Expected TfL CostGroup cost £39.60, got {transit_cg.cost}"


@pytest.mark.asyncio
async def test_commute_selector_impossible_without_bus():
    """When transit fails and bus_result is not an active dep
    (default None in compute), _impossible() must not crash."""
    from houses.nodes.commute import CommuteSelectorNode

    origin = UserInputNode[GeoPoint]("origin_nb", GeoPoint)
    poi = UserInputNode[PlaceOfInterest]("poi_nb", PlaceOfInterest)
    transit = UserInputNode[dict]("transit_nb", dict)
    _succeeded_walk_check(False)

    # Provide a bus node so the constructor doesn't get None as dep,
    # but walk_check returns False so bus is NOT an active dep.
    UserInputNode[dict]("bus_nb", dict)
    # Don't push bus — it'll be pending, but not added to active deps

    node = CommuteSelectorNode(
        "commute_nb",
        origin=origin,
        poi=poi,
        transit_result=transit,
        max_walk=30,
    )

    origin.push(GeoPoint(51.5, -0.1), "user")
    poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
    # Don't push transit — it'll be pending, so the selector can't
    # run compute.
    await flush_processor()

    a = await node.attempt()
    assert a.pending, f"Expected pending, got {a.status}: {a.error}"


@pytest.mark.asyncio
async def test_walk_selected_when_fastest():
    """Walk is correctly selected when it's the fastest option.

    Previously, when a bus_result parameter existed, it shifted walk_result
    into the `drive` position in compute(). With bus_result removed, the
    deps order matches compute()'s signature, so walk arrives in `walk`.
    """
    from houses.nodes.commute import CommuteSelectorNode

    origin = UserInputNode[GeoPoint]("org_ws", GeoPoint)
    poi = UserInputNode[PlaceOfInterest]("poi_ws", PlaceOfInterest)
    transit = FixedCommuteNode("transit_ws")
    rail_fare = FixedCommuteNode("rail_fare_ws")
    walk = FixedCommuteNode("walk_ws")

    node = CommuteSelectorNode(
        "walk_selected_test",
        origin=origin,
        poi=poi,
        transit_result=transit,
        walk_result=walk,
        max_walk=30,
    )

    origin.push(GeoPoint(51.5, -0.1), "user")
    poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

    # Walk: 10 min, £0 — fastest, should win
    walk.push(_make_commute(duration_min=10, cost_gbp=0))
    # Transit: 40 min, £5
    transit.push(_make_commute(duration_min=40, cost_gbp=5))
    # Rail fare not needed (transit has cost > 0)
    rail_fare.push(_make_commute(duration_min=40, cost_gbp=5))

    await flush_processor()

    a = await node.attempt()
    assert a.succeeded, f"Expected succeeded, got {a.status}: {a.error}"
    val = a.value_or_none()
    assert val is not None
    # Walk (10 min) is faster than transit (40 min), so walk should be selected
    assert val.duration.magnitude == 10, f"Expected 10 min (walk), got {val.duration}"
    assert float(val.daily_cost.amount) == 0, f"Expected £0, got £{val.daily_cost.amount}"


class TestRailFareNodeErrorPropagation:
    """RailFareNode.compute must propagate the transit error reason.

    Regression: it returned generic "transit not succeeded", hiding the
    real TfL error (e.g. 409) from the frontend provenance.
    """

    @pytest.mark.asyncio
    async def test_propagates_transit_error(self):
        from dag.user_input_node import UserInputNode
        from houses.nodes.rail_fare_node import RailFareNode

        transit = UserInputNode("rf_transit", object)
        transit.push(_make_commute(duration_min=30, cost_gbp=5), "test")

        class _FailTransit(DerivedNode[Commute]):
            def __init__(self):
                super().__init__("rf_fail", Commute, deps=())

            def compute(self):
                raise AssertionError("should not run")

            async def attempt(self):
                return Attempt.impossible("TfL API returned 409 Conflict: route planner unavailable")

        location = UserInputNode("rf_loc", object)
        location.push(GeoPoint(51.5, -0.1), "test")

        node = RailFareNode(
            "rf/node",
            transit_result=_FailTransit(),
            best_location=location,
        )
        # Bypass the framework's dep short-circuit — call compute directly
        # with an impossible transit attempt, as compute() receives it when
        # the dep check is bypassed.
        result = await node.compute(
            Attempt.impossible("TfL API returned 409 Conflict: route planner unavailable"),
            Attempt.succeeded(GeoPoint(51.5, -0.1)),
        )
        assert result.impossible
        assert "409" in result.error, f"Expected 409 in error, got: {result.error}"


class TestNoRouteCommuteChain:
    """Drive-only destinations: a transit "no route" answer is a
    succeeded-infeasible commute, never an impossible attempt — so the
    selector falls back to drive and the rail-fare chain survives
    without framework short-circuits or crashy .details access."""

    def _infeasible_commute(self, label: str = "NoRoute") -> Commute:
        return Commute(
            person=Person(name="Simon", has_car=True),
            label=label,
            destination=PlaceOfInterest(label=label, address="RG12 8YA"),
            duration=Quantity(0, "minute"),  # type: ignore[arg-type]
            daily_cost=Money("0", "GBP"),
            mode="transit",
            _details=(),
            infeasible=True,
        )

    def test_needs_rail_fare_false_for_infeasible(self):
        from houses.nodes.commute import _needs_rail_fare

        assert _needs_rail_fare(Attempt.succeeded(self._infeasible_commute())) is False

    @pytest.mark.asyncio
    async def test_rail_fare_if_survives_infeasible_transit(self):
        """Infeasible transit → _needs_rail_fare False → else branch → succeeded(None),
        NOT a short-circuited impossible."""

        class _FixedTransit(DerivedNode[Commute]):
            def __init__(self, commute: Commute):
                super().__init__("_rf_fixed_transit", Commute, ())
                self._att = Attempt.succeeded(commute)

            async def attempt(self):
                return self._att

            def latest_attempt(self):
                return self._att

            def compute(self, *dep_attempts):
                raise AssertionError("fixed node should not compute")

        transit = _FixedTransit(self._infeasible_commute())
        then_branch = _impossible_commute("rail_fare")
        node = _rail_fare_if(transit, then_branch)
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded, f"rail_fare_if should give succeeded(None), got: {a.status}: {a.error}"
        assert a.value_or_none() is None

    @pytest.mark.asyncio
    async def test_merge_passes_drive_through_when_rail_fare_none(self):
        """Selector picks drive; rail fare is None; merge must pass the drive
        commute through unchanged."""
        from houses.nodes.commute import MergeRailFareNode

        drive = _drive_commute(duration_min=35, cost_gbp=5.0)
        commute_src = FixedCommuteNode("_mrg_drive")
        commute_src.set(drive)

        class _NoneFare(DerivedNode[Commute]):
            def __init__(self, node_id: str):
                super().__init__(node_id, Commute, ())

            async def attempt(self):
                return Attempt.succeeded(None)

            def latest_attempt(self):
                return Attempt.succeeded(None)

            def compute(self, *dep_attempts):
                raise AssertionError("fixed node should not compute")

        node = MergeRailFareNode(
            "_mrg_no_fare", commute_result=commute_src, rail_fare_result=_NoneFare("_mrg_fare_none")
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded, f"merge should pass drive through, got: {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None and val.duration.magnitude == 35

    @pytest.mark.asyncio
    async def test_full_chain_drive_only_destination(self):
        """The user's bug: transit no-route + drive-only destination → the
        selector picks drive and the merge/final chain keeps it."""
        from dag.if_then_else import IfThenElseNode
        from houses.nodes.commute import MergeRailFareNode
        from houses.nodes.commute import _needs_rail_fare as _needs_rail_fare_fn

        class _Fixed(DerivedNode[Commute]):
            def __init__(self, node_id: str, attempt: Attempt[Commute]):
                super().__init__(node_id, Commute, ())
                self._att = attempt

            async def attempt(self):
                return self._att

            def latest_attempt(self):
                return self._att

            def compute(self, *dep_attempts):
                raise AssertionError("fixed node should not compute")

        origin = UserInputNode[GeoPoint]("chain_origin", GeoPoint)
        origin.push(GeoPoint(51.5, -0.1), "test")
        poi = UserInputNode[str]("chain_poi", str)
        poi.push("RG12 8YA", "test")

        from houses.nodes.commute import CommuteSelectorNode

        transit_no_route = _Fixed("chain_transit", Attempt.succeeded(self._infeasible_commute("Bracknell")))
        walk_no_route = _Fixed("chain_walk", Attempt.succeeded(self._infeasible_commute("walk")))
        drive = _drive_commute(duration_min=35, cost_gbp=5.0)
        drive_src = FixedCommuteNode("chain_drive")
        drive_src.set(drive)

        selector = CommuteSelectorNode(
            "chain/commute",
            origin=origin,
            poi=poi,
            transit_result=transit_no_route,
            walk_result=walk_no_route,
            drive_result=drive_src,
            is_child=False,
            max_walk=30,
        )
        rail_fare_if = IfThenElseNode(
            "chain/rail_fare_if",
            Commute | None,  # type: ignore[arg-type]
            condition_sources=(transit_no_route,),
            condition_fn=_needs_rail_fare_fn,
            then_branch=_impossible_commute("rail_fare"),
        )
        merge = MergeRailFareNode("chain/merge", commute_result=selector, rail_fare_result=rail_fare_if)
        await flush_processor()
        a = await merge.attempt()
        assert a.succeeded, f"chain should end in drive commute, got: {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None and val.duration.magnitude == 35


class TestFareConditionalDependency:
    """The rail-fare input of MergeRailFareNode is a CONDITIONAL dependency:
    a drive/walk selection never activates it — the fare node stays
    pending (never calculated) and its status can't affect the merge."""

    def _transit_commute(self, duration_min: int = 120) -> Commute:
        """A feasible transit commute with unpriced train legs (needs fare)."""
        office = PlaceOfInterest("Office", "SW1V 2QQ")
        person = Person("Simon", True, places_of_interest=(office,))
        leg = JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(duration_min, "minute"), end_station="Reading")  # type: ignore[arg-type]
        return Commute(
            person=person,
            label="Bracknell",
            destination=office,
            duration=Quantity(duration_min, "minute"),  # type: ignore[arg-type]

            daily_cost=Money("0", "GBP"),  # unpriced → needs NR fare
            _details=(CostGroup(legs=(leg,), operator="TfL", cost=None),),
        )

    @pytest.mark.asyncio
    async def test_drive_selection_never_activates_fare_dependency(self):
        """Bracknell case: selector picks drive; the fare node must stay
        PENDING — never calculated — and the merge keeps the drive commute."""
        from dag.if_then_else import IfThenElseNode
        from houses.nodes.commute import CommuteSelectorNode, MergeRailFareNode
        from houses.nodes.commute import _needs_rail_fare as _needs_rail_fare_fn

        class _Fixed(DerivedNode[Commute]):
            def __init__(self, node_id: str, attempt: Attempt[Commute]):
                super().__init__(node_id, Commute, ())
                self._att = attempt

            async def attempt(self):
                return self._att

            def latest_attempt(self):
                return self._att

            def compute(self, *dep_attempts):
                raise AssertionError("fixed node should not compute")

        origin = UserInputNode[GeoPoint]("cdd_origin", GeoPoint)
        origin.push(GeoPoint(51.5, -0.1), "test")
        poi = UserInputNode[str]("cdd_poi", str)
        poi.push("RG12 8YA", "test")

        transit = _Fixed("cdd_transit", Attempt.succeeded(self._transit_commute()))
        walk = _Fixed(
            "cdd_walk",
            Attempt.succeeded(
                Commute(
                    person=Person(name="Simon", has_car=True),
                    label="walk",
                    destination=PlaceOfInterest(label="walk", address=""),
                    duration=Quantity(0, "minute"),  # type: ignore[arg-type]

                    daily_cost=Money("0", "GBP"),
                    mode="walk",
                    _details=(),
                    infeasible=True,
                )
            ),
        )
        drive_src = FixedCommuteNode("cdd_drive")
        drive_src.set(_drive_commute(duration_min=16, cost_gbp=5.0))

        selector = CommuteSelectorNode(
            "cdd/commute",
            origin=origin,
            poi=poi,
            transit_result=transit,
            walk_result=walk,
            drive_result=drive_src,
            is_child=False,
            max_walk=30,
        )
        class _WouldFailFare(DerivedNode[Commute]):
            def __init__(self, node_id: str):
                super().__init__(node_id, Commute, ())

            def compute(self, *dep_attempts):
                raise AssertionError("fare node must never be calculated for a drive selection")

        fare_node = _WouldFailFare("cdd_fare_never_run")  # unique id — no persisted row
        rail_fare_if = IfThenElseNode(
            "cdd/rail_fare_if",
            Commute | None,  # type: ignore[arg-type]
            condition_sources=(transit, selector),
            condition_fn=_needs_rail_fare_fn,
            then_branch=fare_node,
        )
        merge = MergeRailFareNode("cdd/merge", commute_result=selector, rail_fare_result=rail_fare_if)
        await flush_processor()

        sel_a = await selector.attempt()
        assert sel_a.succeeded
        _sv = sel_a.value_or_none()
        assert _sv is not None
        assert _sv.mode == "drive"

        # The fare branch never activated: rail_fare_if is succeeded(None)
        # and the RailFareNode is not among its active deps — the fare
        # lookup is never awaited for a drive selection.
        fare_a = await rail_fare_if.attempt()
        assert fare_a.succeeded, f"rail_fare_if should give succeeded(None), got: {fare_a.status}: {fare_a.error}"
        assert fare_a.value_or_none() is None
        active = rail_fare_if._get_active_deps()
        assert fare_node not in active, "fare node must not be an active dependency for a drive selection"

        a = await merge.attempt()
        assert a.succeeded, f"merge must keep the drive commute, got: {a.status}: {a.error}"
        _v = a.value_or_none()
        assert _v is not None
        assert _v.duration.magnitude == 16

    @pytest.mark.asyncio
    async def test_transit_selection_activates_fare_dependency(self):
        """Selected commute is transit → the fare dependency activates and is
        applied."""
        from dag.if_then_else import IfThenElseNode
        from houses.nodes.commute import CommuteSelectorNode, MergeRailFareNode
        from houses.nodes.commute import _needs_rail_fare as _needs_rail_fare_fn

        class _Fixed(DerivedNode[Commute]):
            def __init__(self, node_id: str, attempt: Attempt[Commute]):
                super().__init__(node_id, Commute, ())
                self._att = attempt

            async def attempt(self):
                return self._att

            def latest_attempt(self):
                return self._att

            def compute(self, *dep_attempts):
                raise AssertionError("fixed node should not compute")

        origin = UserInputNode[GeoPoint]("cda_origin", GeoPoint)
        origin.push(GeoPoint(51.5, -0.1), "test")
        poi = UserInputNode[str]("cda_poi", str)
        poi.push("RG12 8YA", "test")

        transit = _Fixed("cda_transit", Attempt.succeeded(self._transit_commute(90)))
        walk = _Fixed(
            "cda_walk",
            Attempt.succeeded(
                Commute(
                    person=Person(name="Simon", has_car=True),
                    label="walk",
                    destination=PlaceOfInterest(label="walk", address=""),
                    duration=Quantity(0, "minute"),  # type: ignore[arg-type]

                    daily_cost=Money("0", "GBP"),
                    mode="walk",
                    _details=(),
                    infeasible=True,
                )
            ),
        )
        drive_src = FixedCommuteNode("cda_drive")
        drive_src.set(_drive_commute(duration_min=300, cost_gbp=50.0))  # slow+expensive → transit wins

        selector = CommuteSelectorNode(
            "cda/commute",
            origin=origin,
            poi=poi,
            transit_result=transit,
            walk_result=walk,
            drive_result=drive_src,
            is_child=False,
            max_walk=30,
        )
        # A fare node that returns a priced transit group
        from houses.nodes.rail_fare_node import RailFareNode  # noqa: F401  (import for side effects)

        class _Fare(DerivedNode[Commute]):
            def __init__(self, node_id: str):
                super().__init__(node_id, Commute | None, ())  # type: ignore[arg-type]
                self._att = Attempt.pending()

            async def attempt(self):
                if self._att.pending:
                    fare_leg = JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(90, "minute"), end_station="Reading")  # type: ignore[arg-type]
                    self._att = Attempt.succeeded(
                        Commute(
                            person=Person(name="", has_car=False),
                            label="fare",
                            destination=PlaceOfInterest(label="", address=""),
                            duration=Quantity(90, "minute"),  # type: ignore[arg-type]

                            daily_cost=Money("9.90", "GBP"),
                            mode="transit",
                            _details=(CostGroup(legs=(fare_leg,), operator="NR", cost=Money("9.90", "GBP")),),
                        )
                    )
                return self._att

            def latest_attempt(self):
                return self._att

            def compute(self, *dep_attempts):
                raise AssertionError("fixed node should not compute")

        rail_fare_if = IfThenElseNode(
            "cda/rail_fare_if",
            Commute | None,  # type: ignore[arg-type]
            condition_sources=(transit, selector),
            condition_fn=_needs_rail_fare_fn,
            then_branch=_Fare("cda_fare"),
        )
        merge = MergeRailFareNode("cda/merge", commute_result=selector, rail_fare_result=rail_fare_if)
        await flush_processor()

        sel_a = await selector.attempt()
        assert sel_a.succeeded
        _sv = sel_a.value_or_none()
        assert _sv is not None
        assert _sv.mode == "transit"

        a = await merge.attempt()
        assert a.succeeded, f"merge should apply the fare to the transit commute, got: {a.status}: {a.error}"
        _v = a.value_or_none()
        assert _v is not None
        assert _v.daily_cost == Money("9.90", "GBP")


class TestCommuteChainProvenanceFormula:
    """The merge and breakdown calc cards must carry formula visualisations."""

    @pytest.mark.asyncio
    async def test_merge_formula_lists_commute_and_fare(self):
        from houses.nodes.commute import MergeRailFareNode

        transit = _make_commute(duration_min=60, cost_gbp=5.0)
        commute_src = FixedCommuteNode("mf_commute")
        commute_src.set(transit)

        class _Fare(DerivedNode[Commute]):
            def __init__(self, node_id: str):
                super().__init__(node_id, Commute | None, ())  # type: ignore[arg-type]
                self._att = Attempt.pending()

            async def attempt(self):
                if self._att.pending:
                    fare_leg = JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(30, "minute"))  # type: ignore[arg-type]
                    self._att = Attempt.succeeded(
                        Commute(
                            person=Person(name="", has_car=False),
                            label="fare",
                            destination=PlaceOfInterest(label="", address=""),
                            duration=Quantity(30, "minute"),  # type: ignore[arg-type]
                            daily_cost=Money("9.90", "GBP"),
                            mode="transit",
                            _details=(CostGroup(legs=(fare_leg,), operator="NR", cost=Money("9.90", "GBP")),),
                        )
                    )
                return self._att

            def latest_attempt(self):
                return self._att

            def compute(self, *dep_attempts):
                raise AssertionError("fixed node should not compute")

        node = MergeRailFareNode("mf_node", commute_result=commute_src, rail_fare_result=_Fare("mf_fare"))
        await flush_processor()
        prov = await node.build_provenance()
        assert prov.formula is not None
        assert [line.label for line in prov.formula.lines] == ["Commute", "Rail fare"]

    @pytest.mark.asyncio
    async def test_breakdown_formula_lists_per_person(self):
        from houses.nodes.commute_breakdown_node import CommuteBreakdownNode

        persons_src = UserInputNode("bf_persons", list)
        persons_src.push(
            [
                {
                    "name": "Simon",
                    "places_of_interest": [
                        PlaceOfInterest(label="Bracknell", address="", trips_per_week=1, weeks_per_year=46)
                    ],
                }
            ],
            "test",
        )
        commute_src = FixedCommuteNode("bf_commute")
        commute_src.set(_drive_commute(duration_min=16, cost_gbp=5.0))
        node = CommuteBreakdownNode(
            "bf_node", commute_selectors={"Simon/Bracknell": commute_src}, persons_source=persons_src
        )
        await flush_processor()
        prov = await node.build_provenance()
        assert prov.formula is not None
        assert [line.label for line in prov.formula.lines] == ["Simon’s commute (yearly)"]
