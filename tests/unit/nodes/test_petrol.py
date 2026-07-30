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
        # Base cost 5.00 + fuel cost (48 km / (282.5/45) L/100km * 1.45 £/L)
        litres_per_100km = 282.5 / 45
        fuel_litres = 48 / litres_per_100km
        fuel_cost = fuel_litres * 1.45
        expected = round(5.00 + fuel_cost, 2)
        assert float(val.daily_cost.amount) == expected, (
            f"Expected daily_cost {expected}, got {val.daily_cost.amount}"
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
            _make_commute(
                duration_min=30, cost_gbp=5.00, mode="drive", drive_legs_minutes=[30]
            ),
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
        litres_per_100km = 282.5 / 30
        fuel_litres = 48 / litres_per_100km
        fuel_cost = fuel_litres * 1.60
        expected = round(5.00 + fuel_cost, 2)
        assert float(val.daily_cost.amount) == expected
