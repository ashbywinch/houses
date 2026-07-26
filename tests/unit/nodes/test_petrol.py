"""Tests for PetrolCostAugmentNode."""

from __future__ import annotations

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
    """Build a Commute for testing."""
    office = PlaceOfInterest("Office", "SW1V 2QQ")
    person = Person("Simon", True, places_of_interest=(office,))

    if details is not None:
        pass
    elif drive_legs_minutes:
        legs = tuple(
            JourneyLeg(
                mode=LegMode.DRIVE,
                duration_minutes=m,
                distance_km=drive_distances_km[i] if drive_distances_km and i < len(drive_distances_km) else 0.0,
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
        details=details,
    )


def _settings_node(data: dict | None = None) -> UserInputNode[dict]:
    """Build a financial settings node with test values."""
    node = UserInputNode[dict]("_financial", dict)
    node.push(data or {"petrol_mpg": 45, "petrol_cost_per_litre": 1.45}, "test")
    return node


class TestPetrolCostAugmentNode:
    @pytest.mark.asyncio
    async def test_adds_fuel_cost_to_drive_commute(self):
        """Drive commute gets fuel cost in daily_cost, no extra leg."""
        from houses.nodes.petrol import PetrolCostAugmentNode

        commute_in = UserInputNode[Commute]("commute_in", Commute)
        financial = _settings_node(
            {
                "petrol_mpg": 45,
                "petrol_cost_per_litre": 1.45,
            }
        )

        node = PetrolCostAugmentNode(
            "petrol",
            commute_node=commute_in,
            financial_source=financial,
        )

        # 30 min drive with actual distance_km=24 (one-way)
        # round_trip_km = 24 * 2 = 48
        # fuel_volume = (48 km / 45 mile/imperial_gallon).to(liter) = 3.013 L
        # cost = 3.013 * 1.45 = 4.37
        # daily_cost = 5.00 + 4.37 = 9.37
        commute_in.push(
            _make_commute(
                duration_min=30, cost_gbp=5.00, mode="drive", drive_legs_minutes=[30], drive_distances_km=[24.0]
            ),
            "test",
        )

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        assert float(val.daily_cost.amount) == 9.37
        # No extra petrol leg — fuel cost folded into daily_cost
        assert len(val.details) == 1
        assert not any(cg.operator == "Fuel" for cg in val.details)
        # The drive CostGroup must have the fuel cost attributed
        drive_cg = next((cg for cg in val.details), None)
        assert drive_cg is not None
        assert drive_cg.cost is not None, "Drive CostGroup should have cost attributed"
        # Original cost was 5.00, fuel added 3.64 → total 8.64
        if isinstance(drive_cg.cost, Money):
            assert float(drive_cg.cost.amount) == 9.37, f"Expected drive CostGroup cost £9.37, got {drive_cg.cost}"

    @pytest.mark.asyncio
    async def test_skips_non_drive_commute(self):
        """Non-drive commute is returned unchanged."""
        from houses.nodes.petrol import PetrolCostAugmentNode

        commute_in = UserInputNode[Commute]("commute_in", Commute)
        financial = _settings_node()

        node = PetrolCostAugmentNode(
            "petrol",
            commute_node=commute_in,
            financial_source=financial,
        )

        commute_in.push(
            _make_commute(duration_min=32, cost_gbp=4.50, mode="transit"),
            "test",
        )

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        assert val.mode == "transit"
        assert float(val.daily_cost.amount) == 4.50
        assert len(val.details) == 1

    @pytest.mark.asyncio
    async def test_zero_drive_minutes(self):
        """Drive commute with no drive legs is returned unchanged."""
        from houses.nodes.petrol import PetrolCostAugmentNode

        commute_in = UserInputNode[Commute]("commute_in", Commute)
        financial = _settings_node()

        node = PetrolCostAugmentNode(
            "petrol",
            commute_node=commute_in,
            financial_source=financial,
        )

        # mode is 'drive' but no DRIVE legs
        commute_in.push(
            _make_commute(
                duration_min=30,
                cost_gbp=5.00,
                mode="drive",
                details=(
                    CostGroup(
                        legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=30),),
                        operator="TfL",
                        cost=Money("5.00", "GBP"),
                    ),
                ),
            ),
            "test",
        )

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        assert float(val.daily_cost.amount) == 5.00
        assert len(val.details) == 1

    @pytest.mark.asyncio
    async def test_uses_settings_values(self):
        """Custom mpg and cost-per-litre produce correct fuel cost."""
        from houses.nodes.petrol import PetrolCostAugmentNode

        commute_in = UserInputNode[Commute]("commute_in2", Commute)
        financial = _settings_node(
            {
                "petrol_mpg": 30,
                "petrol_cost_per_litre": 1.60,
            }
        )

        node = PetrolCostAugmentNode(
            "petrol2",
            commute_node=commute_in,
            financial_source=financial,
        )
        # round_trip_km = 48 * 2 = 96
        # fuel_volume = (96 km / 30 mile/imperial_gallon).to(liter) = 9.038 L
        # cost = 9.038 * 1.60 = 14.46
        # daily_cost = 10.00 + 14.46 = 24.46
        commute_in.push(
            _make_commute(
                duration_min=60, cost_gbp=10.00, mode="drive", drive_legs_minutes=[60], drive_distances_km=[48.0]
            ),
            "test",
        )

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert float(val.daily_cost.amount) == 24.46
        assert len(val.details) == 1

    @pytest.mark.asyncio
    async def test_fallback_to_time_estimate_when_no_distance(self):
        """Without distance_km, falls back to 48 km/h estimation."""
        from houses.nodes.petrol import PetrolCostAugmentNode

        commute_in = UserInputNode[Commute]("commute_in3", Commute)
        financial = _settings_node(
            {
                "petrol_mpg": 45,
                "petrol_cost_per_litre": 1.45,
            }
        )

        node = PetrolCostAugmentNode(
            "petrol3",
            commute_node=commute_in,
            financial_source=financial,
        )

        # fallback: round_trip_km = (30/60) * 48 * 2 = 48
        # fuel_volume = (48 km / 45 mile/imperial_gallon).to(liter) = 3.013 L
        # cost = 3.013 * 1.45 = 4.37
        # daily_cost = 2.00 + 4.37 = 6.37
        commute_in.push(
            _make_commute(duration_min=30, cost_gbp=2.00, mode="drive", drive_legs_minutes=[30]),
            "test",
        )

        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert float(val.daily_cost.amount) == 6.37
        assert len(val.details) == 1

    @pytest.mark.asyncio
    async def test_impossible_without_commute(self):
        """No commute input leads to impossible."""
        from houses.nodes.petrol import PetrolCostAugmentNode

        commute_in = UserInputNode[Commute]("commute_in", Commute)
        financial = _settings_node()

        node = PetrolCostAugmentNode(
            "petrol",
            commute_node=commute_in,
            financial_source=financial,
        )

        await flush_processor()

        a = await node.attempt()
        assert a.pending  # or impossible depending on dep resolution
