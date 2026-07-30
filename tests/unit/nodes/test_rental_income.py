"""Tests the rental income editing UX in CostsSection."""

from __future__ import annotations

import pytest
from money import Money

from dag.user_input_node import UserInputNode
from houses.nodes.property import PropertyNodes


class TestRentalIncomeEditability:
    """Rental income must be editable in the UI after the settings refactor."""

    @pytest.mark.asyncio
    async def test_rental_income_appears_in_detail(self):
        """Rental income must appear in the property detail response."""
        rid = "ri_test"
        prop = PropertyNodes(rid)
        from dag.scheduler import flush_processor

        prop.rental_income.push(Money("500", "GBP"), "user")
        await flush_processor()
        detail = await prop.to_json_detail()
        aff = detail.get("affordability", {})
        ri = aff.get("rental_income")
        assert ri is not None, "rental_income missing from affordability"
        assert ri.get("succeeded"), f"rental_income not succeeded: {ri}"
        val = ri.get("value")
        assert val is not None, f"rental_income has no value: {ri}"
        assert float(val["amount"]) == 500, f"Expected rental income 500, got {val}"

    @pytest.mark.asyncio
    async def test_rental_income_patch_updates_detail(self):
        """PATCH to rental income must be reflected on re-read."""
        rid = "ri_test2"
        prop = PropertyNodes(rid)
        from dag.scheduler import flush_processor

        # Push initial value
        prop.rental_income.push(Money("200", "GBP"), "user")
        await flush_processor()

        # Push updated value (simulating PATCH endpoint)
        prop.rental_income.push(Money("800", "GBP"), "user")
        await flush_processor()

        detail = await prop.to_json_detail()
        ri = detail["affordability"]["rental_income"]
        assert ri.get("succeeded"), f"rental_income not succeeded after patch: {ri}"
        val = ri.get("value")
        assert val is not None
        assert float(val["amount"]) == 800, f"Expected 800 after patch, got {val}"
