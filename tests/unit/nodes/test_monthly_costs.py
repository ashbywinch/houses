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

        node = MonthlyMortgagePaymentNode(
            "mm", rightmove_price=price, financial_source=fin,
        )
        price.push("0", "test")
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

        node = MonthlyMortgagePaymentNode(
            "mm2", rightmove_price=price, financial_source=fin,
        )
        price.push("300000", "test")
        fin.push({
            "mortgage_rate": 0.045,
            "mortgage_term_years": 30,
        }, "test")
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
            "ys", rightmove_price=price, financial_source=fin,
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
            "ys2", rightmove_price=price, financial_source=fin,
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

        src_simon = UserInputNode[dict]("simon", dict)
        src_brac = UserInputNode[dict]("brac", dict)
        src_lorena = UserInputNode[dict]("lorena", dict)
        persons = UserInputNode[list]("persons_cb", list)

        node = CommuteBreakdownNode(
            "cb",
            simon_office=src_simon,
            simon_bracknell=src_brac,
            lorena_office=src_lorena,
            persons_source=persons,
        )
        src_simon.push({}, "test")
        src_brac.push({}, "test")
        src_lorena.push({}, "test")
        persons.push([{"name": "Simon", "places_of_interest": []}], "test")
        await flush_processor()
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
