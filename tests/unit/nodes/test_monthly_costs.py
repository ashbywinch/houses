from __future__ import annotations

import pytest
from money import Money

from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.council_tax_info import CouncilTaxInfo


class TestMonthlyMortgagePaymentNode:
    @pytest.mark.asyncio
    async def test_zero_when_no_price(self):
        from houses.nodes.monthly_mortgage_payment_node import MonthlyMortgagePaymentNode

        price = UserInputNode[Money]("price_mm", Money)
        fin = UserInputNode[dict]("fin_mm", dict)
        sd = UserInputNode[Money]("sd_mm", Money)
        persons = UserInputNode[list]("ps_mm", list)
        node = MonthlyMortgagePaymentNode(
            "mm",
            rightmove_price=price,
            stamp_duty_node=sd,
            persons_source=persons,
            financial_source=fin,
        )
        price.push(Money("0", "GBP"), "test")
        sd.push(Money("0", "GBP"), "test")
        persons.push([], "test")
        fin.push({}, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("0", "GBP")

    @pytest.mark.asyncio
    async def test_computes_with_valid_data(self):
        from houses.nodes.monthly_mortgage_payment_node import MonthlyMortgagePaymentNode

        price = UserInputNode[Money]("price_mm2", Money)
        fin = UserInputNode[dict]("fin_mm2", dict)
        sd = UserInputNode[Money]("sd_mm2", Money)
        persons = UserInputNode[list]("ps_mm2", list)
        node = MonthlyMortgagePaymentNode(
            "mm2",
            rightmove_price=price,
            stamp_duty_node=sd,
            persons_source=persons,
            financial_source=fin,
        )
        price.push(Money("300000", "GBP"), "test")
        sd.push(Money("0", "GBP"), "test")
        persons.push([], "test")
        fin.push(
            {
                "mortgage_rate": 0.045,
                "mortgage_term_years": 30,
            },
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() > Money("0", "GBP")


class TestYearlySinkingFundNode:
    @pytest.mark.asyncio
    async def test_zero_when_no_price(self):
        from houses.nodes.yearly_sinking_fund_node import YearlySinkingFundNode

        price = UserInputNode[Money]("price_ys", Money)
        fin = UserInputNode[dict]("fin_ys", dict)
        node = YearlySinkingFundNode(
            "ys",
            rightmove_price=price,
            financial_source=fin,
        )
        price.push(Money("0", "GBP"), "test")
        fin.push({}, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("0", "GBP")

    @pytest.mark.asyncio
    async def test_computes_with_price(self):
        from houses.nodes.yearly_sinking_fund_node import YearlySinkingFundNode

        price = UserInputNode[Money]("price_ys2", Money)
        fin = UserInputNode[dict]("fin_ys2", dict)
        node = YearlySinkingFundNode(
            "ys2",
            rightmove_price=price,
            financial_source=fin,
        )
        price.push(Money("500000", "GBP"), "test")
        fin.push({"sinking_fund_rate": 0.01}, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("5000", "GBP")


class TestCommuteBreakdownNode:
    @pytest.mark.asyncio
    async def test_returns_defaults_when_no_commutes(self):
        from houses.nodes.commute_breakdown_node import CommuteBreakdownNode

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
        assert a.value_or_none()["yearly_total_gbp"] == "0"


class TestTotalMonthlyHousingCostNode:
    @pytest.mark.asyncio
    async def test_returns_zero_when_no_data(self):
        from houses.nodes.total_monthly_housing_cost_node import TotalMonthlyHousingCostNode

        mg = UserInputNode[Money]("mg", Money)
        sf = UserInputNode[Money]("sf", Money)
        fin = UserInputNode[dict]("fin_tm", dict)
        cb = UserInputNode[dict]("cb_tm", dict)
        ct = UserInputNode[CouncilTaxInfo]("ct_tm", CouncilTaxInfo)
        node = TotalMonthlyHousingCostNode(
            "tm",
            monthly_mortgage_node=mg,
            yearly_sinking_fund_node=sf,
            financial_source=fin,
            commute_breakdown_node=cb,
            council_tax_node=ct,
        )
        mg.push(Money("0", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        fin.push({}, "test")
        cb.push({}, "test")
        ct.push(CouncilTaxInfo(), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert isinstance(a.value_or_none(), Money)

    @pytest.mark.asyncio
    async def test_includes_all_cost_components(self):
        """Each cost component contributes to the total.
        Replaces the old spreadsheet formula test that checked
        named range references in VIEW_FORMULA_COLS."""
        from houses.nodes.total_monthly_housing_cost_node import TotalMonthlyHousingCostNode

        mg = UserInputNode[Money]("mg2", Money)
        sf = UserInputNode[Money]("sf2", Money)
        fin = UserInputNode[dict]("fin2", dict)
        cb = UserInputNode[dict]("cb2", dict)
        ct = UserInputNode[CouncilTaxInfo]("ct2", CouncilTaxInfo)
        node = TotalMonthlyHousingCostNode(
            "tm2",
            monthly_mortgage_node=mg,
            yearly_sinking_fund_node=sf,
            financial_source=fin,
            commute_breakdown_node=cb,
            council_tax_node=ct,
        )

        # Mortgage alone contributes 1000
        mg.push(Money("1000", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        fin.push({}, "test")
        cb.push({}, "test")
        ct.push(CouncilTaxInfo(), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.value_or_none() == Money("1000", "GBP")

        # Sinking fund: 1200 / 12 * 2 / 3 = 66.67
        sf.push(Money("1200", "GBP"), "test")
        await flush_processor()
        a = await node.attempt()
        expected = round(1000.0 + 1200.0 / 12 * 2 / 3, 2)
        assert float(a.value_or_none().amount) == pytest.approx(expected, abs=0.01)

        # Commute: yearly_total_gbp = 4600 / 12 = 383.33
        cb.push({"yearly_total_gbp": 4600.0}, "test")
        await flush_processor()
        a = await node.attempt()
        expected = round(1000.0 + 1200.0 / 12 * 2 / 3 + 4600.0 / 12, 2)
        assert float(a.value_or_none().amount) == pytest.approx(expected, abs=0.01)

        # Council tax: yearly_cost 1800 / 12 = 150
        ct.push(CouncilTaxInfo(band="D", yearly_cost=Money("1800", "GBP")), "test")
        await flush_processor()
        a = await node.attempt()
        expected = round(1000.0 + 1200.0 / 12 * 2 / 3 + 4600.0 / 12 + 1800.0 / 12, 2)
        assert float(a.value_or_none().amount) == pytest.approx(expected, abs=0.01)
