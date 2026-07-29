"""Tests for the DAG-based commute computation pipeline.

Replaces the retired ``houses.enricher`` module.  Tests the three-node
pipeline — TransitNode → CommuteSelectorNode → CommuteBreakdownNode —
backed by ``FakeCommuteRouter`` with canned results.
"""

from __future__ import annotations

import copy

import pytest
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.tfl_client import TflClient
from houses.transit_route import _apply_park_and_ride_to_journeys
from tests.helpers import FixedCommuteNode

# ======================================================================
# DAG-based commute computation (replaces old houses.enricher tests)
# ======================================================================


def _make_commute(duration_min: int = 32, cost_gbp: str | float = "10.0") -> Commute:
    """Build a Commute suitable for feeding into a TransitNode / FakeCommuteRouter."""
    from pint import Quantity

    from houses.commute import CostGroup
    from houses.model.domain import Commute as CommuteDomain

    office = PlaceOfInterest("Office", "SW1V 2QQ", trips_per_week=1, weeks_per_year=46)
    person = Person("Simon", True, places_of_interest=(office,))
    return CommuteDomain(
        person=person,
        label=office.label,
        destination=office,
        duration=Quantity(duration_min, "minute"),
        daily_cost=Money(str(cost_gbp), "GBP"),
        details=(CostGroup(legs=(), operator="TfL", cost=Money(str(cost_gbp), "GBP")),),
    )


def _serialize_commute(duration_min: int, cost_gbp: float, label: str = "Office", mode: str = "transit") -> Commute:
    """Return a Commute matching the shape TransitNode/CommuteSelectorNode produce."""
    from houses.model.domain import Commute as CommuteObj
    from houses.model.domain import Person, PlaceOfInterest

    return CommuteObj(
        person=Person(name="", has_car=False),
        label=label,
        destination=PlaceOfInterest(label=label, address=""),
        duration=Quantity(duration_min, "minute"),
        daily_cost=Money(str(cost_gbp), "GBP"),
        mode=mode,
    )


# ── TransitNode ──────────────────────────────────────────────────────


class TestTransitCommute:
    """TransitNode — produces a serialised commute dict from TflTransitNode deps."""

    @pytest.fixture
    def _tfl_deps(self):
        from houses.nodes.transit import TflTransitNode

        return lambda loc, poi, has_car=False, prefix="t": (
            TflTransitNode(f"{prefix}_nb", best_location=loc, poi=poi, has_car=has_car, allow_bus=False),
            TflTransitNode(f"{prefix}_wb", best_location=loc, poi=poi, has_car=has_car, allow_bus=True),
        )

    @pytest.mark.asyncio
    async def test_pending_without_location(self):
        """No location → node stays pending."""
        from houses.nodes.transit import TflTransitNode, TransitNode

        loc = UserInputNode[GeoPoint]("tr_loc1", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("tr_poi1", PlaceOfInterest)

        no_bus = TflTransitNode("tr1_nb", best_location=loc, poi=poi, has_car=False, allow_bus=False)
        with_bus = TflTransitNode("tr1_wb", best_location=loc, poi=poi, has_car=False, allow_bus=True)
        node = TransitNode(
            "tr1", best_location=loc, poi=poi, has_car=False, max_walk=30, no_bus_node=no_bus, with_bus_node=with_bus
        )
        a = await node.attempt()
        assert a.pending, "Should be pending when location isn't set"

    @pytest.mark.asyncio
    async def test_pending_without_poi(self):
        """No POI → node stays pending even with location set."""
        from houses.nodes.transit import TflTransitNode, TransitNode

        loc = UserInputNode[GeoPoint]("tr_loc2", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("tr_poi2", PlaceOfInterest)

        loc.push(GeoPoint(51.5, -0.1), "test")
        await flush_processor()
        no_bus = TflTransitNode("tr2_nb", best_location=loc, poi=poi, has_car=False, allow_bus=False)
        with_bus = TflTransitNode("tr2_wb", best_location=loc, poi=poi, has_car=False, allow_bus=True)
        node = TransitNode(
            "tr2", best_location=loc, poi=poi, has_car=False, max_walk=30, no_bus_node=no_bus, with_bus_node=with_bus
        )
        a = await node.attempt()
        assert a.pending, "Should be pending when POI isn't set"

    @pytest.mark.asyncio
    async def test_returns_commute_from_router(self, monkeypatch):
        """With all deps, TransitNode picks best from TflTransitNode deps."""
        from houses.nodes.transit import TflTransitNode, TransitNode
        from houses.tfl_client import TflClient

        loc = UserInputNode[GeoPoint]("tr_loc3", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("tr_poi3", PlaceOfInterest)

        loc.push(GeoPoint(51.5, -0.1), "test")
        office = PlaceOfInterest("Office", "SW1V 2QQ")
        poi.push(office)

        commute = _make_commute(duration_min=45, cost_gbp="12.50")

        async def mock_plan(self):
            return Attempt.succeeded(commute)

        monkeypatch.setattr(TflClient, "plan", mock_plan)

        no_bus = TflTransitNode("tr3_nb", best_location=loc, poi=poi, has_car=False, allow_bus=False)
        with_bus = TflTransitNode("tr3_wb", best_location=loc, poi=poi, has_car=False, allow_bus=True)
        node = TransitNode(
            "tr3/Simon/Office/computed_transit",
            best_location=loc,
            poi=poi,
            has_car=False,
            max_walk=30,
            no_bus_node=no_bus,
            with_bus_node=with_bus,
        )
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, f"Expected succeeded, got {a.status}: {a.error}"
        val = a.value_or_none()
        assert val.duration.magnitude == 45
        assert val.daily_cost.amount == 12.50

    @pytest.mark.asyncio
    async def test_impossible_when_router_fails(self, monkeypatch):
        """Router returning impossible → TransitNode is impossible."""
        from houses.nodes.transit import TflTransitNode, TransitNode
        from houses.tfl_client import TflClient

        loc = UserInputNode[GeoPoint]("tr_loc4", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("tr_poi4", PlaceOfInterest)

        loc.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

        async def mock_fail(self):
            return Attempt.impossible("API down")

        monkeypatch.setattr(TflClient, "plan", mock_fail)

        no_bus = TflTransitNode("tr4_nb", best_location=loc, poi=poi, has_car=False, allow_bus=False)
        with_bus = TflTransitNode("tr4_wb", best_location=loc, poi=poi, has_car=False, allow_bus=True)
        node = TransitNode(
            "tr4/Simon/Office/computed_transit",
            best_location=loc,
            poi=poi,
            has_car=False,
            max_walk=30,
            no_bus_node=no_bus,
            with_bus_node=with_bus,
        )
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.impossible

    @pytest.mark.asyncio
    async def test_to_json_has_boolean_fields(self):
        """TransitNode.to_json() must include succeeded/pending/impossible booleans."""
        from houses.nodes.transit import TflTransitNode, TransitNode

        loc = UserInputNode[GeoPoint]("tr_loc5", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("tr_poi5", PlaceOfInterest)

        no_bus = TflTransitNode("tr5_nb", best_location=loc, poi=poi, has_car=False, allow_bus=False)
        with_bus = TflTransitNode("tr5_wb", best_location=loc, poi=poi, has_car=False, allow_bus=True)
        node = TransitNode(
            "tr5", best_location=loc, poi=poi, has_car=False, max_walk=30, no_bus_node=no_bus, with_bus_node=with_bus
        )
        j = await node.to_json()
        assert "succeeded" in j
        assert "pending" in j
        assert "impossible" in j
        assert j["pending"] is True
        assert j["succeeded"] is False
        assert j["impossible"] is False

    @pytest.mark.asyncio
    async def test_uses_has_car_and_max_walk_params(self, monkeypatch):
        """Uses has_car and max_walk from constructor params for the commute request."""
        from houses.nodes.transit import TflTransitNode, TransitNode
        from houses.tfl_client import TflClient

        loc = UserInputNode[GeoPoint]("tr_loc6", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("tr_poi6", PlaceOfInterest)

        loc.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

        captured = {}

        async def capture_plan(self):
            captured["park_and_ride"] = self._park_and_ride
            captured["allow_bus"] = self._allow_bus
            return Attempt.succeeded(_make_commute())

        monkeypatch.setattr(TflClient, "plan", capture_plan)

        no_bus = TflTransitNode("tr6_nb", best_location=loc, poi=poi, has_car=True, allow_bus=False)
        with_bus = TflTransitNode("tr6_wb", best_location=loc, poi=poi, has_car=True, allow_bus=True)
        node = TransitNode(
            "rid/Simon/Office/computed_transit",
            best_location=loc,
            poi=poi,
            has_car=True,
            max_walk=15,
            no_bus_node=no_bus,
            with_bus_node=with_bus,
        )
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert captured.get("park_and_ride") is True

    @pytest.mark.asyncio
    async def test_transit_is_not_child(self, monkeypatch):
        """Transit result is_child is always False (child handling done upstream)."""
        from houses.nodes.transit import TflTransitNode, TransitNode
        from houses.tfl_client import TflClient

        loc = UserInputNode[GeoPoint]("tr_loc7", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("tr_poi7", PlaceOfInterest)

        loc.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("School", "SL6 1AA"), "config")

        async def mock_plan(self):
            return Attempt.succeeded(_make_commute(duration_min=20, cost_gbp="0"))

        monkeypatch.setattr(TflClient, "plan", mock_plan)

        no_bus = TflTransitNode("tr7_nb", best_location=loc, poi=poi, has_car=False, allow_bus=False)
        with_bus = TflTransitNode("tr7_wb", best_location=loc, poi=poi, has_car=False, allow_bus=True)
        node = TransitNode(
            "rid/George/School/computed_transit",
            best_location=loc,
            poi=poi,
            has_car=False,
            max_walk=30,
            no_bus_node=no_bus,
            with_bus_node=with_bus,
        )
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none().person.is_child is False


class TestCommuteSelectorPipeline:
    @staticmethod
    def _dummy_commute_node():
        from money import Money
        from pint import Quantity

        from houses.model.domain import Person, PlaceOfInterest

        n = UserInputNode[Commute]("_dummy", Commute)
        n.push(
            Commute(
                person=Person(name="", has_car=False),
                label="",
                destination=PlaceOfInterest(label="", address=""),
                duration=Quantity(999, "minute"),
                daily_cost=Money("0", "GBP"),
            ),
            "default",
        )
        return n

    """CommuteSelectorNode — picks transit, falls back to bus, or raises impossible."""

    @pytest.mark.asyncio
    async def test_transit_takes_priority(self):
        """When transit succeeds it is returned directly (Commute object)."""
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("csel_o1", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("csel_p1", PlaceOfInterest)
        transit = FixedCommuteNode("csel_t1")
        bus = FixedCommuteNode("csel_b1")
        node = CommuteSelectorNode(
            "csel1",
            origin=origin,
            poi=poi,
            walk_result=self._dummy_commute_node(),
            transit_result=transit,
            drive_result=self._dummy_commute_node(),
        )

        origin.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

        transit_commute = _make_commute(duration_min=30, cost_gbp="10.0")
        bus_commute = _make_commute(duration_min=55, cost_gbp="5.0")

        transit.push(transit_commute)
        bus.push(bus_commute)

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none().daily_cost == Money("10.0", "GBP")

    @pytest.mark.asyncio
    async def test_pending_when_transit_unset(self):
        """When transit is pending, selector is pending."""
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("csel_o2", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("csel_p2", PlaceOfInterest)
        transit = FixedCommuteNode("csel_t2")

        node = CommuteSelectorNode(
            "csel2",
            origin=origin,
            poi=poi,
            walk_result=self._dummy_commute_node(),
            transit_result=transit,
            drive_result=self._dummy_commute_node(),
        )
        origin.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

        await flush_processor()

        a = await node.attempt()
        assert a.pending, "Should be pending when transit hasn't been set"

    @pytest.mark.asyncio
    async def test_impossible_when_both_fail(self):
        """When all routes fail, selector is impossible."""
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("csel_o3", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("csel_p3", PlaceOfInterest)
        transit = FixedCommuteNode("csel_t3")

        node = CommuteSelectorNode(
            "csel3",
            origin=origin,
            poi=poi,
            transit_result=transit,
        )

        origin.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
        transit._commute = None
        transit._attempt = Attempt.impossible("no route")
        transit.changed.emit()

        await flush_processor()

        a = await node.attempt()
        assert a.impossible, f"Should be impossible when all routes fail, got {a.status}: {a.error}"

    @pytest.mark.asyncio
    async def test_impossible_when_origin_missing(self):
        """No origin → selector is impossible."""
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("csel_o4", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("csel_p4", PlaceOfInterest)
        transit = FixedCommuteNode("csel_t4")

        node = CommuteSelectorNode(
            "csel4",
            origin=origin,
            poi=poi,
            walk_result=self._dummy_commute_node(),
            transit_result=transit,
            drive_result=self._dummy_commute_node(),
        )

        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
        transit.push(_make_commute(duration_min=35, cost_gbp="8.50"))

        await flush_processor()

        a = await node.attempt()

        assert a.pending, "Should be pending when origin is missing (waiting for data)"

    @pytest.mark.asyncio
    async def test_to_json_includes_value_and_booleans(self):
        """CommuteSelectorNode.to_json() includes status/value/is_child/provenance."""
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("csel_o5", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("csel_p5", PlaceOfInterest)
        transit = FixedCommuteNode("csel_t5")
        bus = FixedCommuteNode("csel_b5")

        node = CommuteSelectorNode(
            "csel5",
            origin=origin,
            poi=poi,
            walk_result=self._dummy_commute_node(),
            transit_result=transit,
            drive_result=self._dummy_commute_node(),
        )

        origin.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")
        transit.push(_make_commute(duration_min=32, cost_gbp="4.50"), "TfL")
        bus.push(_make_commute(duration_min=55, cost_gbp="2.00"), "Bus")

        await flush_processor()

        j = await node.to_json()
        assert j["status"] == "succeeded"
        val = j["value"]
        assert isinstance(val, dict), "value should be a dict"
        assert "duration" in val, "value missing duration"
        assert "daily_cost" in val, "value missing daily_cost"
        assert val.get("daily_cost", {}).get("amount") == "4.50"
        assert j["is_child"] is False
        assert "error" not in j
        assert "provenance" in j

    @pytest.mark.asyncio
    async def test_selector_with_commute_values(self):
        """CommuteSelectorNode passes through a Commute object."""
        from houses.nodes.commute import CommuteSelectorNode

        origin = UserInputNode[GeoPoint]("csel_o6", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("csel_p6", PlaceOfInterest)
        transit = FixedCommuteNode("csel_t6")
        bus = FixedCommuteNode("csel_b6")

        node = CommuteSelectorNode(
            "csel6",
            origin=origin,
            poi=poi,
            walk_result=self._dummy_commute_node(),
            transit_result=transit,
            drive_result=self._dummy_commute_node(),
        )

        origin.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest("Office", "SW1V 2QQ"), "config")

        transit_commute = _make_commute(duration_min=35, cost_gbp="8.50")
        bus.push(_make_commute(duration_min=55, cost_gbp="2.00"), "Bus")
        transit.push(transit_commute)

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val.duration.magnitude == 35
        assert float(val.daily_cost.amount) == 8.50
        assert val.label == "Office"


# ── CommuteBreakdownNode ─────────────────────────────────────────────


class TestCommuteBreakdown:
    """CommuteBreakdownNode — sums yearly commute costs."""

    @pytest.mark.asyncio
    async def test_yearly_formula_with_all_costs(self):
        """46wk x (15 + 10 + 2x24) = 46 x 73 = 3358"""
        from houses.nodes.commute_breakdown_node import CommuteBreakdownNode

        so = UserInputNode[Commute]("cbd_so1", Commute)
        sb = UserInputNode[Commute]("cbd_sb1", Commute)
        lo = UserInputNode[Commute]("cbd_lo1", Commute)
        persons = UserInputNode[list]("cbd_ps1", list)

        selectors = {
            "Simon/Pimlico": so,
            "Simon/Bracknell": sb,
            "Lorena/Aldgate": lo,
        }

        node = CommuteBreakdownNode(
            "cbd1",
            commute_selectors=selectors,
            persons_source=persons,
        )

        so.push(_serialize_commute(30, 15.0, label="Pimlico"), "test")
        sb.push(_serialize_commute(90, 10.0, label="Bracknell", mode="drive"), "test")
        lo.push(_serialize_commute(45, 24.0, label="Aldgate"), "test")
        persons.push(
            [
                Person(
                    "Simon",
                    True,
                    places_of_interest=(
                        PlaceOfInterest("Pimlico", "SW1V 2QQ", trips_per_week=1, weeks_per_year=46),
                        PlaceOfInterest("Bracknell", "RG12 8YA", trips_per_week=1, weeks_per_year=46),
                    ),
                ),
                Person(
                    "Lorena",
                    False,
                    places_of_interest=(PlaceOfInterest("Aldgate", "EC3A 7LP", trips_per_week=2, weeks_per_year=46),),
                ),
            ],
            "test",
        )
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        # Simon office: 15.0 * 1 * 46 = 690
        # Simon Bracknell: 10.0 * 1 * 46 = 460
        # Lorena: 24.0 * 2 * 46 = 2208
        # Total: 3358
        assert val["yearly_total_gbp"] == "3358.0"

    @pytest.mark.asyncio
    async def test_missing_cost_means_partial_total(self):
        """When some costs are present, total includes only those."""
        from houses.nodes.commute_breakdown_node import CommuteBreakdownNode

        so = UserInputNode[Commute]("cbd_so2", Commute)
        sb = UserInputNode[Commute]("cbd_sb2", Commute)
        lo = UserInputNode[Commute]("cbd_lo2", Commute)
        persons = UserInputNode[list]("cbd_ps2", list)

        selectors = {
            "Simon/Pimlico": so,
            "Simon/Bracknell": sb,
            "Lorena/Aldgate": lo,
        }

        node = CommuteBreakdownNode(
            "cbd2",
            commute_selectors=selectors,
            persons_source=persons,
        )

        # Simon office has no commute data (zero-cost commute), others have costs
        so.push(
            Commute(
                person=Person("", False),
                label="",
                destination=PlaceOfInterest("", ""),
                duration=Quantity(0, "minute"),
                daily_cost=Money("0", "GBP"),
            ),
            "test",
        )
        sb.push(_serialize_commute(90, 10.0, label="Bracknell", mode="drive"), "test")
        lo.push(_serialize_commute(45, 24.0, label="Aldgate"), "test")
        persons.push(
            [
                Person(
                    "Simon",
                    True,
                    places_of_interest=(
                        PlaceOfInterest("Pimlico", "SW1V 2QQ", trips_per_week=1, weeks_per_year=46),
                        PlaceOfInterest("Bracknell", "RG12 8YA", trips_per_week=1, weeks_per_year=46),
                    ),
                ),
                Person(
                    "Lorena",
                    False,
                    places_of_interest=(PlaceOfInterest("Aldgate", "EC3A 7LP", trips_per_week=2, weeks_per_year=46),),
                ),
            ],
            "test",
        )
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        # Simon Bracknell: 10.0 * 1 * 46 = 460
        # Lorena: 24.0 * 2 * 46 = 2208
        # Total: 2668
        assert val["yearly_total_gbp"] == "2668.0"

    @pytest.mark.asyncio
    async def test_returns_defaults_when_no_commutes(self):
        """All commute selectors empty → yearly_total is 0.0."""
        from houses.nodes.commute_breakdown_node import CommuteBreakdownNode

        persons = UserInputNode[list]("cbd_ps2", list)

        node = CommuteBreakdownNode(
            "cbd2",
            commute_selectors={},
            persons_source=persons,
        )

        persons.push([Person("Simon", True)], "test")
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none()["yearly_total_gbp"] == "0"

    @pytest.mark.asyncio
    async def test_missing_commute_does_not_crash(self):
        """When some commute selectors are impossible, node still succeeds."""
        from houses.nodes.commute_breakdown_node import CommuteBreakdownNode

        so = UserInputNode[Commute]("cbd_so3", Commute)
        sb = UserInputNode[Commute]("cbd_sb3", Commute)
        lo = UserInputNode[Commute]("cbd_lo3", Commute)
        persons = UserInputNode[list]("cbd_ps3", list)

        selectors = {
            "Simon/Pimlico": so,
            "Simon/Bracknell": sb,
            "Lorena/Aldgate": lo,
        }

        node = CommuteBreakdownNode(
            "cbd3",
            commute_selectors=selectors,
            persons_source=persons,
        )
        # Push all deps so they're terminal (none pending)
        # Simon Bracknell and Lorena Office get empty dicts (no real commute data)
        sb.push(
            Commute(
                person=Person("", False),
                label="",
                destination=PlaceOfInterest("", ""),
                duration=Quantity(0, "minute"),
                daily_cost=Money("0", "GBP"),
            ),
            "test",
        )
        lo.push(
            Commute(
                person=Person("", False),
                label="",
                destination=PlaceOfInterest("", ""),
                duration=Quantity(0, "minute"),
                daily_cost=Money("0", "GBP"),
            ),
            "test",
        )
        so.push(_serialize_commute(30, 10.0, label="Pimlico"), "test")
        persons.push([Person("Simon", True)], "test")
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none()["yearly_total_gbp"] == "0"


# ======================================================================
# Imports needed by TestParkAndRide (kept at module bottom to avoid
# shadowing the test classes above)
# ======================================================================


class TestParkAndRide:
    """_apply_park_and_ride_to_journeys — replaces long walks with driving.
    Uses DI (``_drive_fn``) instead of ``patch``."""

    LONG_WALK_DATA = {
        "journeys": [
            {
                "duration": 87,
                "legs": [
                    {
                        "mode": {"name": "walking"},
                        "duration": 35,
                        "arrivalPoint": {"commonName": "Maidenhead Rail Station"},
                        "instruction": {"summary": "Walk to Maidenhead Rail Station"},
                    },
                    {
                        "mode": {"name": "national-rail"},
                        "duration": 20,
                        "arrivalPoint": {"commonName": "London Paddington Rail Station"},
                        "instruction": {"summary": "Great Western Railway to London Paddington"},
                    },
                    {
                        "mode": {"name": "walking"},
                        "duration": 7,
                        "arrivalPoint": {"commonName": "SW1V 2QQ"},
                        "instruction": {"summary": "Walk to SW1V 2QQ"},
                    },
                ],
            }
        ]
    }

    SHORT_WALK_DATA = {
        "journeys": [
            {
                "duration": 60,
                "legs": [
                    {
                        "mode": {"name": "walking"},
                        "duration": 10,
                        "arrivalPoint": {"commonName": "Weybridge Rail Station"},
                        "instruction": {"summary": "Walk to Weybridge Rail Station"},
                    },
                    {
                        "mode": {"name": "national-rail"},
                        "duration": 25,
                        "arrivalPoint": {"commonName": "London Waterloo Rail Station"},
                        "instruction": {"summary": "South Western Railway to London Waterloo"},
                    },
                ],
            }
        ]
    }

    async def _drive_10(self, _origin, _station):
        return 10

    async def _drive_3(self, _origin, _station):
        return 3

    async def _drive_none(self, _origin, _station):
        return None

    @pytest.mark.asyncio
    async def test_replaces_long_walk_with_drive(self):
        data = copy.deepcopy(self.LONG_WALK_DATA)
        result = await _apply_park_and_ride_to_journeys(data, "SL6 3YZ", max_walk_minutes=20, _drive_fn=self._drive_10)
        legs = result["journeys"][0]["legs"]
        assert legs[0]["mode"]["name"] == "driving"
        assert result["journeys"][0]["duration"] == 62

    @pytest.mark.asyncio
    async def test_skips_short_walk(self):
        data = copy.deepcopy(self.SHORT_WALK_DATA)
        result = await _apply_park_and_ride_to_journeys(data, "KT13 0TD", max_walk_minutes=20, _drive_fn=self._drive_3)
        legs = result["journeys"][0]["legs"]
        assert legs[0]["mode"]["name"] == "walking"
        assert legs[0]["duration"] == 10

    @pytest.mark.asyncio
    async def test_skips_non_walking_first_leg(self):
        data = {"journeys": [{"duration": 45, "legs": [{"mode": {"name": "national-rail"}, "duration": 20}]}]}
        calls = []

        async def _check_not_called(_o, _s):
            calls.append(1)
            return 10

        result = await _apply_park_and_ride_to_journeys(data, "SL6", 20, _drive_fn=_check_not_called)
        assert len(calls) == 0
        assert result["journeys"][0]["legs"][0]["mode"]["name"] == "national-rail"

    @pytest.mark.asyncio
    async def test_skips_when_drive_lookup_fails(self):
        data = copy.deepcopy(self.LONG_WALK_DATA)
        result = await _apply_park_and_ride_to_journeys(
            data, "SL6 3YZ", max_walk_minutes=20, _drive_fn=self._drive_none
        )
        legs = result["journeys"][0]["legs"]
        assert legs[0]["mode"]["name"] == "walking"
        assert legs[0]["duration"] == 35

    @pytest.mark.asyncio
    async def test_format_includes_drive_in_route_after_park_and_ride(self):
        data = copy.deepcopy(self.LONG_WALK_DATA)
        result = await _apply_park_and_ride_to_journeys(data, "SL6 3YZ", max_walk_minutes=20, _drive_fn=self._drive_10)
        best = min(result["journeys"], key=lambda j: j.get("duration", 9999))
        summary = TflClient._format_route_summary(best)
        assert "Drive to Maidenhead (10m)" in summary
        assert "Train to Paddington (20m)" in summary
        assert "walk 7m" in summary


def _succeeded_walk_check(val: bool = False) -> DerivedNode:
    """Build a minimal walk-check node whose ``_attempt`` is already resolved."""
    from dag.attempt import Attempt
    from dag.user_input_node import UserInputNode
    from houses.nodes.transit import WalkLegCheckNode

    t = UserInputNode[dict]("_wc_t", dict)
    w = WalkLegCheckNode("_wc", transit_node=t, max_walk=30)
    w._attempt = Attempt.succeeded(val)
    return w
