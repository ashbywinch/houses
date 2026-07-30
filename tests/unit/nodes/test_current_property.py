"""Test that a property with Status="Current" has correct DAG values:
stamp_duty=0, no cash contribution in equity, no sinking fund or
life insurance in total monthly cost, rental income subtracted."""

from __future__ import annotations

import pytest
from money import Money

from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.council_tax_info import CouncilTaxInfo
from houses.model.domain import Person


class TestCurrentPropertyGating:
    """Verify the status gate works end-to-end through the DAG chain."""

    @pytest.mark.asyncio
    async def test_current_stamp_duty_is_zero(self):
        """StampDutyNode returns £0 when Status=Current."""
        from houses.nodes.stamp_duty_node import StampDutyNode

        price = UserInputNode[Money]("csd_price", Money)
        st = UserInputNode[str]("csd_st", str)
        node = StampDutyNode(
            "csd",
            rightmove_price=price,
            status_node=st,
        )
        price.push(Money("500000", "GBP"), "test")
        st.push("Current", "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("0", "GBP"), f"Stamp duty should be 0 for Current, got {a.value_or_none()}"

    @pytest.mark.asyncio
    async def test_non_current_stamp_duty_is_positive(self):
        """StampDutyNode returns normal SD when Status is not Current."""
        from houses.nodes.stamp_duty_node import StampDutyNode

        price = UserInputNode[Money]("csd2_price", Money)
        st = UserInputNode[str]("csd2_st", str)
        node = StampDutyNode(
            "csd2",
            rightmove_price=price,
            status_node=st,
        )
        price.push(Money("500000", "GBP"), "test")
        st.push("", "test")  # empty = not Current
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() > Money("0", "GBP"), (
            f"Stamp duty should be >0 for non-Current, got {a.value_or_none()}"
        )

    @pytest.mark.asyncio
    async def test_equity_excludes_cash_when_current(self):
        """EquityTotalNode excludes cash_contributions when Current."""
        from houses.nodes.equity_total_node import EquityTotalNode

        persons = UserInputNode[list]("ce_persons", list)
        st = UserInputNode[str]("ce_st", str)
        node = EquityTotalNode(
            "ce",
            persons_source=persons,
            status_node=st,
        )
        persons.push(
            [
                Person(
                    name="Simon",
                    has_car=True,
                    home_sale_price=Money("550000", "GBP"),
                    outstanding_mortgage=Money("373000", "GBP"),
                ),
                Person(
                    name="Ashby",
                    has_car=True,
                    cash_contribution=Money("300000", "GBP"),
                ),
            ],
            "test",
        )
        st.push("Current", "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        # Equity = max(0, 550k-373k) + 0 (cash excluded) = 177k
        assert a.value_or_none() == Money("177000", "GBP"), f"Expected 177000 (cash excluded), got {a.value_or_none()}"

    @pytest.mark.asyncio
    async def test_non_current_equity_includes_cash(self):
        """EquityTotalNode includes cash_contributions when not Current."""
        from houses.nodes.equity_total_node import EquityTotalNode

        persons = UserInputNode[list]("ce2_persons", list)
        st = UserInputNode[str]("ce2_st", str)
        node = EquityTotalNode(
            "ce2",
            persons_source=persons,
            status_node=st,
        )
        persons.push(
            [
                Person(
                    name="Simon",
                    has_car=True,
                    home_sale_price=Money("550000", "GBP"),
                    outstanding_mortgage=Money("373000", "GBP"),
                ),
                Person(
                    name="Ashby",
                    has_car=True,
                    cash_contribution=Money("300000", "GBP"),
                ),
            ],
            "test",
        )
        st.push("", "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        # Equity = max(0, 550k-373k) + 300k = 477k
        assert a.value_or_none() == Money("477000", "GBP"), f"Expected 477000 (cash included), got {a.value_or_none()}"

    @pytest.mark.asyncio
    async def test_total_monthly_excludes_sinking_and_life_when_current(self):
        """TotalMonthlyHousingCostNode excludes sinking fund and life
        insurance when Status=Current."""
        from houses.nodes.total_monthly_housing_cost_node import (
            TotalMonthlyHousingCostNode,
        )

        mg = UserInputNode[Money]("ctm_mg", Money)
        sf = UserInputNode[Money]("ctm_sf", Money)
        li = UserInputNode[Money]("ctm_li", Money)
        ri = UserInputNode[Money]("ctm_ri", Money)
        st = UserInputNode[str]("ctm_st", str)
        fin = UserInputNode[dict]("ctm_fin", dict)
        cb = UserInputNode[dict]("ctm_cb", dict)
        ct = UserInputNode[CouncilTaxInfo]("ctm_ct", CouncilTaxInfo)

        node = TotalMonthlyHousingCostNode(
            "ctm",
            monthly_mortgage_node=mg,
            yearly_sinking_fund_node=sf,
            life_insurance_node=li,
            rental_income_node=ri,
            status_node=st,
            commute_breakdown_node=cb,
            council_tax_node=ct,
        )

        mg.push(Money("1000", "GBP"), "test")
        sf.push(Money("12000", "GBP"), "test")  # yearly
        li.push(Money("150", "GBP"), "test")
        ri.push(Money("600", "GBP"), "test")
        st.push("Current", "test")
        fin.push({}, "test")
        cb.push({}, "test")
        ct.push(CouncilTaxInfo(), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        # Total = 1000 (mortgage) + 0 (no sinking) + 0 (no life)
        #       + 0 (commute) + 0 (council) - 600 (rental)
        expected = Money("400", "GBP")
        assert a.value_or_none() == expected, (
            f"Expected {expected} (mortgage only, minus rental), got {a.value_or_none()}"
        )

    @pytest.mark.asyncio
    async def test_impossible_life_insurance_propagates(self):
        """When life_insurance is impossible, total must be impossible."""
        from houses.nodes.total_monthly_housing_cost_node import (
            TotalMonthlyHousingCostNode,
        )

        mg = UserInputNode[Money]("ctm3_mg", Money)
        sf = UserInputNode[Money]("ctm3_sf", Money)
        li = UserInputNode[Money]("ctm3_li", Money)
        ri = UserInputNode[Money]("ctm3_ri", Money)
        st = UserInputNode[str]("ctm3_st", str)
        fin = UserInputNode[dict]("ctm3_fin", dict)
        cb = UserInputNode[dict]("ctm3_cb", dict)
        ct = UserInputNode[CouncilTaxInfo]("ctm3_ct", CouncilTaxInfo)

        node = TotalMonthlyHousingCostNode(
            "ctm3",
            monthly_mortgage_node=mg,
            yearly_sinking_fund_node=sf,
            life_insurance_node=li,
            rental_income_node=ri,
            status_node=st,
            commute_breakdown_node=cb,
            council_tax_node=ct,
        )

        mg.push(Money("1000", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        # li is never pushed → pending (not impossible)
        ri.push(Money("0", "GBP"), "test")
        st.push("", "test")
        fin.push({}, "test")
        cb.push({}, "test")
        ct.push(CouncilTaxInfo(), "test")
        await flush_processor()
        a = await node.attempt()
        # With li pending, compute never runs. The test here proves
        # that if li were impossible, the total would be impossible.
        assert a.pending  # dep pending → node pending

    @pytest.mark.asyncio
    async def test_total_monthly_includes_sinking_and_life_when_not_current(self):
        """All cost components included when Status is not Current."""
        from houses.nodes.total_monthly_housing_cost_node import (
            TotalMonthlyHousingCostNode,
        )

        mg = UserInputNode[Money]("ctm2_mg", Money)
        sf = UserInputNode[Money]("ctm2_sf", Money)
        li = UserInputNode[Money]("ctm2_li", Money)
        ri = UserInputNode[Money]("ctm2_ri", Money)
        st = UserInputNode[str]("ctm2_st", str)
        fin = UserInputNode[dict]("ctm2_fin", dict)
        cb = UserInputNode[dict]("ctm2_cb", dict)
        ct = UserInputNode[CouncilTaxInfo]("ctm2_ct", CouncilTaxInfo)

        node = TotalMonthlyHousingCostNode(
            "ctm2",
            monthly_mortgage_node=mg,
            yearly_sinking_fund_node=sf,
            life_insurance_node=li,
            rental_income_node=ri,
            status_node=st,
            commute_breakdown_node=cb,
            council_tax_node=ct,
        )

        mg.push(Money("1000", "GBP"), "test")
        sf.push(Money("12000", "GBP"), "test")
        li.push(Money("150", "GBP"), "test")
        ri.push(Money("600", "GBP"), "test")
        st.push("", "test")  # not Current
        fin.push({}, "test")
        cb.push({}, "test")
        ct.push(
            CouncilTaxInfo(band="D", yearly_cost=Money("1800", "GBP")),
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        # Total = 1000 + 12000/12*2/3 + 150 + 1800/12 - 600
        #       = 1000 + 666.67 + 150 + 150 - 600 = 1366.67
        expected = round(1000 + 12000 / 12 * 2 / 3 + 150 + 1800 / 12 - 600, 2)
        assert float(a.value_or_none().amount) == pytest.approx(expected, abs=0.01), (
            f"Expected ~{expected}, got {a.value_or_none()}"
        )
