from __future__ import annotations

import pytest

from dag.derived_node import flush_processor
from dag.user_input_node import UserInputNode


class TestMonthlyMortgagePaymentNode:
    @pytest.mark.asyncio
    async def test_zero_when_no_price(self):
        from houses.nodes.monthly_costs import MonthlyMortgagePaymentNode

        price = UserInputNode[str]("price_mm", str)
        fin = UserInputNode[dict]("fin_mm", dict)
        sd = UserInputNode[float]("sd_mm", float)
        persons = UserInputNode[list]("ps_mm", list)

        node = MonthlyMortgagePaymentNode(
            "mm",
            rightmove_price=price,
            stamp_duty_node=sd,
            persons_source=persons,
            financial_source=fin,
        )
        price.push("0", "test")
        sd.push(0.0, "test")
        persons.push([], "test")
        fin.push({}, "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == 0.0

    @pytest.mark.asyncio
    async def test_computes_with_valid_data(self):
        from houses.nodes.monthly_costs import MonthlyMortgagePaymentNode

        price = UserInputNode[str]("price_mm2", str)
        fin = UserInputNode[dict]("fin_mm2", dict)
        sd = UserInputNode[float]("sd_mm2", float)
        persons = UserInputNode[list]("ps_mm2", list)

        node = MonthlyMortgagePaymentNode(
            "mm2",
            rightmove_price=price,
            stamp_duty_node=sd,
            persons_source=persons,
            financial_source=fin,
        )
        price.push("300000", "test")
        sd.push(0.0, "test")
        persons.push([], "test")
        fin.push(
            {
                "mortgage_rate": 0.045,
                "mortgage_term_years": 30,
            },
            "test",
        )
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() > 0


class TestYearlySinkingFundNode:
    @pytest.mark.asyncio
    async def test_zero_when_no_price(self):
        from houses.nodes.monthly_costs import YearlySinkingFundNode

        price = UserInputNode[str]("price_ys", str)
        fin = UserInputNode[dict]("fin_ys", dict)

        node = YearlySinkingFundNode(
            "ys",
            rightmove_price=price,
            financial_source=fin,
        )
        price.push("0", "test")
        fin.push({}, "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == 0.0

    @pytest.mark.asyncio
    async def test_computes_with_price(self):
        from houses.nodes.monthly_costs import YearlySinkingFundNode

        price = UserInputNode[str]("price_ys2", str)
        fin = UserInputNode[dict]("fin_ys2", dict)

        node = YearlySinkingFundNode(
            "ys2",
            rightmove_price=price,
            financial_source=fin,
        )
        price.push("500000", "test")
        fin.push({"sinking_fund_rate": 0.01}, "test")
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == 5000.0


class TestCommuteBreakdownNode:
    @pytest.mark.asyncio
    async def test_returns_defaults_when_no_commutes(self):
        from houses.nodes.monthly_costs import CommuteBreakdownNode

        persons = UserInputNode[list]("persons_cb", list)
        selectors = {}

        node = CommuteBreakdownNode(
            "cb",
            commute_selectors=selectors,
            persons_source=persons,
        )
        persons.push([{"name": "Simon", "places_of_interest": []}], "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none()["yearly_total_gbp"] == 0.0


class TestTotalMonthlyHousingCostNode:
    @pytest.mark.asyncio
    async def test_returns_zero_when_no_data(self):
        from houses.nodes.monthly_costs import TotalMonthlyHousingCostNode

        mg = UserInputNode[float]("mg", float)
        sf = UserInputNode[float]("sf", float)
        fin = UserInputNode[dict]("fin_tm", dict)
        cb = UserInputNode[dict]("cb_tm", dict)
        ct = UserInputNode[dict]("ct_tm", dict)

        node = TotalMonthlyHousingCostNode(
            "tm",
            monthly_mortgage_node=mg,
            yearly_sinking_fund_node=sf,
            financial_source=fin,
            commute_breakdown_node=cb,
            council_tax_node=ct,
        )
        mg.push(0.0, "test")
        sf.push(0.0, "test")
        fin.push({}, "test")
        cb.push({}, "test")
        ct.push({}, "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert isinstance(a.value_or_none(), float)

    @pytest.mark.asyncio
    async def test_includes_all_cost_components(self):
        """Each cost component contributes to the total.
        Replaces the old spreadsheet formula test that checked
        named range references in VIEW_FORMULA_COLS."""
        from houses.nodes.monthly_costs import TotalMonthlyHousingCostNode

        mg = UserInputNode[float]("mg2", float)
        sf = UserInputNode[float]("sf2", float)
        fin = UserInputNode[dict]("fin2", dict)
        cb = UserInputNode[dict]("cb2", dict)
        ct = UserInputNode[dict]("ct2", dict)

        node = TotalMonthlyHousingCostNode(
            "tm2",
            monthly_mortgage_node=mg,
            yearly_sinking_fund_node=sf,
            financial_source=fin,
            commute_breakdown_node=cb,
            council_tax_node=ct,
        )

        # Push all deps with initial values so node is computable
        mg.push(0.0, "test")
        sf.push(0.0, "test")
        cb.push({}, "test")
        ct.push({}, "test")
        fin.push({}, "test")
        await flush_processor()
        await flush_processor()

        # Mortgage alone contributes 1000
        mg.push(1000.0, "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == 1000.0

        # Sinking fund: 1200 / 12 * 2 / 3 = 66.67
        sf.push(1200.0, "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        expected = 1000.0 + 1200.0 / 12 * 2 / 3
        assert a.value_or_none() == round(expected, 2)

        # Commute: yearly_total_gbp = 4600 / 46 = 100
        cb.push({"yearly_total_gbp": 4600.0}, "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        expected = 1000.0 + 1200.0 / 12 * 2 / 3 + 4600.0 / 46
        assert a.value_or_none() == round(expected, 2)

        # Council tax: cost 1800 / 12 = 150
        ct.push({"band": "D", "cost": 1800.0}, "test")
        await flush_processor()
        await flush_processor()
        a = await node.attempt()
        expected = 1000.0 + 1200.0 / 12 * 2 / 3 + 4600.0 / 46 + 1800.0 / 12
        assert a.value_or_none() == round(expected, 2)
