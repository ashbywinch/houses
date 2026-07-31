"""Tests for PetrolCostAugmentNode with individual setting nodes."""

from __future__ import annotations

from decimal import Decimal

import pytest
from money import Money
from pint import Quantity

import dag.user_input_node  # noqa: F401 — register Money/Quantity pydantic schemas
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.model.domain import Commute, Person, PlaceOfInterest


def _make_commute(
    duration_min: int = 32,
    cost_gbp: float = 4.50,
    mode: str = "transit",
    details: tuple[CostGroup, ...] | None = None,
    drive_legs_minutes: list[int] | None = None,
    drive_distances_km: list[float] | None = None,
) -> Commute:
    office = PlaceOfInterest("Office", "SW1V 2QQ")
    person = Person("Simon", True, places_of_interest=(office,))
    if details is not None:
        pass
    elif drive_legs_minutes:
        legs = tuple(
            JourneyLeg(
                mode=LegMode.DRIVE,
                duration=Quantity(m, "minute"),
                distance=Quantity(drive_distances_km[i], "km")
                if drive_distances_km and i < len(drive_distances_km)
                else None,
            )
            for i, m in enumerate(drive_legs_minutes)
        )
        details = (CostGroup(legs=legs, operator="TfL", cost=Money(str(cost_gbp), "GBP")),)
    else:
        details = (CostGroup(legs=(), operator="TfL", cost=Money(str(cost_gbp), "GBP")),)
    return Commute(
        person=person,
        label=office.label,
        destination=office,
        duration=Quantity(duration_min, "minute"),
        daily_cost=Money(str(cost_gbp), "GBP"),
        mode=mode,
        _details=details,
    )


def _petrol_mpg_node(value: int = 45) -> UserInputNode:
    """Create a petrol MPG setting node with a value."""
    node = UserInputNode("_mpg", int)
    node.push(value, "test")
    return node


def _petrol_cost_node(value: Decimal = Decimal("1.45")) -> UserInputNode:
    """Create a petrol cost-per-litre setting node with a value."""
    node = UserInputNode("_cost", Decimal)
    node.push(value, "test")
    return node


class TestPetrolCostAugmentNode:
    @pytest.mark.asyncio
    async def test_adds_fuel_cost_to_drive_commute(self):
        from houses.nodes.petrol import PetrolCostAugmentNode

        commute_in = UserInputNode[Commute]("commute_in", Commute)
        node = PetrolCostAugmentNode(
            "petrol",
            commute_node=commute_in,
            petrol_mpg_node=_petrol_mpg_node(45),
            petrol_cost_per_litre_node=_petrol_cost_node(Decimal("1.45")),
        )
        commute_in.push(
            _make_commute(
                duration_min=30, cost_gbp=5.00, mode="drive", drive_legs_minutes=[30], drive_distances_km=[24.0]
            ),
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded, f"Expected succeeded, got {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        # 48 km round trip, 45 MPG, £1.45/L → fuel = £4.37, total = 5.00+4.37 = £9.37
        expected = 9.37
        assert float(val.daily_cost.amount) == expected, (
            f"Expected daily_cost {expected}, got {float(val.daily_cost.amount)}"
        )

    @pytest.mark.asyncio
    async def test_returns_commute_unchanged_when_no_drive_legs(self):
        from houses.nodes.petrol import PetrolCostAugmentNode

        commute_in = UserInputNode[Commute]("commute_in2", Commute)
        node = PetrolCostAugmentNode(
            "petrol2",
            commute_node=commute_in,
            petrol_mpg_node=_petrol_mpg_node(),
            petrol_cost_per_litre_node=_petrol_cost_node(),
        )
        commute_in.push(_make_commute(duration_min=32, mode="transit"), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        assert float(val.daily_cost.amount) == 4.50

    @pytest.mark.asyncio
    async def test_falls_back_to_estimated_distance_when_no_distance_data(self):
        from houses.nodes.petrol import PetrolCostAugmentNode

        commute_in = UserInputNode[Commute]("commute_in3", Commute)
        node = PetrolCostAugmentNode(
            "petrol3",
            commute_node=commute_in,
            petrol_mpg_node=_petrol_mpg_node(45),
            petrol_cost_per_litre_node=_petrol_cost_node(Decimal("1.45")),
        )
        commute_in.push(
            _make_commute(duration_min=30, cost_gbp=5.00, mode="drive", drive_legs_minutes=[30]),
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded, f"Expected succeeded, got {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        assert float(val.daily_cost.amount) > 0

    @pytest.mark.asyncio
    async def test_handles_multiple_drive_legs(self):
        from houses.nodes.petrol import PetrolCostAugmentNode

        commute_in = UserInputNode[Commute]("commute_in4", Commute)
        node = PetrolCostAugmentNode(
            "petrol4",
            commute_node=commute_in,
            petrol_mpg_node=_petrol_mpg_node(45),
            petrol_cost_per_litre_node=_petrol_cost_node(Decimal("1.45")),
        )
        commute_in.push(
            _make_commute(
                duration_min=50,
                cost_gbp=8.00,
                mode="drive",
                drive_legs_minutes=[20, 30],
                drive_distances_km=[16.0, 24.0],
            ),
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded, f"Expected succeeded, got {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        assert float(val.daily_cost.amount) > 8.00

    @pytest.mark.asyncio
    async def test_returns_commute_when_commute_fails(self):
        from houses.nodes.petrol import PetrolCostAugmentNode

        commute_in = UserInputNode[Commute]("commute_in5", Commute)
        node = PetrolCostAugmentNode(
            "petrol5",
            commute_node=commute_in,
            petrol_mpg_node=_petrol_mpg_node(),
            petrol_cost_per_litre_node=_petrol_cost_node(),
        )
        # Don't push a value — commute remains pending
        await flush_processor()
        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_uses_custom_mpg_and_cost(self):
        from houses.nodes.petrol import PetrolCostAugmentNode

        commute_in = UserInputNode[Commute]("commute_in6", Commute)
        node = PetrolCostAugmentNode(
            "petrol6",
            commute_node=commute_in,
            petrol_mpg_node=_petrol_mpg_node(30),
            petrol_cost_per_litre_node=_petrol_cost_node(Decimal("1.60")),
        )
        commute_in.push(
            _make_commute(
                duration_min=30, cost_gbp=5.00, mode="drive", drive_legs_minutes=[30], drive_distances_km=[24.0]
            ),
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded, f"Expected succeeded, got {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        # 48 km round trip, 30 MPG, £1.60/L → fuel = £6.55, total = 5.00+6.55 = £11.55
        expected = 12.23
        assert float(val.daily_cost.amount) == expected, (
            f"Expected daily_cost {expected}, got {float(val.daily_cost.amount)}"
        )


class TestDriveCommuteAlwaysHasCost:
    """A drive commute with a length must always carry petrol cost.
    A succeeded drive-mode commute with daily_cost £0.00 is a bug — the
    drive details must survive from DriveNode to the petrol augment."""

    @pytest.mark.asyncio
    async def test_full_drive_chain_produces_petrol_cost(self):
        """DriveNode → selector → merge → petrol augment must end with a
        drive commute whose daily_cost > 0."""
        from dag.attempt import Attempt
        from dag.derived_node import DerivedNode
        from houses.geo import GeoPoint
        from houses.nodes.bus import BusLegAugmentNode  # noqa: F401
        from houses.nodes.commute import CommuteSelectorNode, MergeRailFareNode
        from houses.nodes.petrol import PetrolCostAugmentNode
        from houses.nodes.transit import DriveNode

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

        def _infeasible(label: str) -> Commute:
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

        async def _drive_route(loc, dest) -> Attempt[Commute]:
            """A real drive route — duration 16 min, 16 km, WITH drive details."""
            leg = JourneyLeg(
                mode=LegMode.DRIVE,
                duration=Quantity(16, "minute"),  # type: ignore[arg-type]
                distance=Quantity(16.0, "km"),  # type: ignore[arg-type]
            )
            return Attempt.succeeded(
                Commute(
                    person=Person(name="Simon", has_car=True),
                    label="Bracknell",
                    destination=PlaceOfInterest(label="Bracknell", address=dest),
                    duration=Quantity(16, "minute"),  # type: ignore[arg-type]
                    daily_cost=Money("0", "GBP"),
                    mode="drive",
                    _details=(CostGroup(legs=(leg,), cost=Money("0", "GBP")),),
                )
            )

        origin = UserInputNode[GeoPoint]("dcc_origin", GeoPoint)
        origin.push(GeoPoint(51.5, -0.1), "test")
        poi = UserInputNode[str]("dcc_poi", str)
        poi.push("RG12 8YA", "test")

        transit = _Fixed("dcc_transit", Attempt.succeeded(_infeasible("transit")))
        walk = _Fixed("dcc_walk", Attempt.succeeded(_infeasible("walk")))
        drive = DriveNode("dcc_drive", best_location=origin, poi=poi, has_car=True, route_fn=_drive_route)

        selector = CommuteSelectorNode(
            "dcc/commute",
            origin=origin,
            poi=poi,
            transit_result=transit,
            walk_result=walk,
            drive_result=drive,
            is_child=False,
            max_walk=30,
        )
        merge = MergeRailFareNode(
            "dcc/merge", commute_result=selector, rail_fare_result=_Fixed("dcc_fare", Attempt.succeeded(None))
        )
        mpg = UserInputNode("dcc_mpg", int)
        mpg.push(45, "test")
        litre = UserInputNode("dcc_litre", float)
        litre.push(1.45, "test")
        petrol = PetrolCostAugmentNode(
            "dcc/final_fuel", commute_node=merge, petrol_mpg_node=mpg, petrol_cost_per_litre_node=litre
        )

        await flush_processor()

        a = await petrol.attempt()
        assert a.succeeded, f"drive chain should succeed, got: {a.status}: {a.error}"
        val = a.value_or_none()
        assert val is not None
        assert val.mode == "drive"
        assert val.daily_cost.amount > 0, (
            f"a {val.duration.magnitude}-minute drive must have petrol cost, got £{val.daily_cost.amount}"
        )


class TestPetrolProvenanceFormula:
    """Petrol Cost calc cards must show the fuel maths, not just a value."""

    @pytest.mark.asyncio
    async def test_formula_shows_distance_and_fuel(self):
        from houses.nodes.petrol import PetrolCostAugmentNode

        commute = _make_commute(
            duration_min=16, cost_gbp=0.0, mode="drive", drive_legs_minutes=[16], drive_distances_km=[16.0]
        )
        src = UserInputNode("pf_src", Commute)
        src.push(commute, "test")
        mpg = _petrol_mpg_node(45)
        cost = UserInputNode("pf_cost", float)
        cost.push(1.45, "test")
        node = PetrolCostAugmentNode("pf_node", commute_node=src, petrol_mpg_node=mpg, petrol_cost_per_litre_node=cost)
        await flush_processor()

        prov = await node.build_provenance()
        assert prov.formula is not None
        labels = [line.label for line in prov.formula.lines]
        assert any("Drive distance" in lab for lab in labels), labels
        assert any(lab.startswith("Fuel:") for lab in labels), labels
        assert prov.formula.result == "GBP 2.91"
