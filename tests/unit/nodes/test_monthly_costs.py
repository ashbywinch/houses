from __future__ import annotations

from decimal import Decimal

import pytest
from money import Money

from dag.measurement import Measurement
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.council_tax_info import CouncilTaxInfo


class TestMonthlyMortgagePaymentNode:
    @pytest.mark.asyncio
    async def test_zero_when_no_principal(self):
        from houses.nodes.monthly_mortgage_payment_node import MonthlyMortgagePaymentNode

        mr = UserInputNode[Money]("mmp_mr1", Money)
        rate = UserInputNode("mmp_rate1", Decimal)
        term = UserInputNode("mmp_term1", int)
        node = MonthlyMortgagePaymentNode(
            "mmp1",
            mortgage_required_node=mr,
            mortgage_rate_node=rate,
            mortgage_term_node=term,
        )
        mr.push(Money("0", "GBP"), "test")
        rate.push(Decimal("0.045"), "test")
        term.push(30, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("0", "GBP")

    @pytest.mark.asyncio
    async def test_computes_with_valid_data(self):
        from houses.nodes.monthly_mortgage_payment_node import MonthlyMortgagePaymentNode

        mr = UserInputNode[Money]("mmp_mr2", Money)
        rate = UserInputNode("mmp_rate2", Decimal)
        term = UserInputNode("mmp_term2", int)
        node = MonthlyMortgagePaymentNode(
            "mmp2",
            mortgage_required_node=mr,
            mortgage_rate_node=rate,
            mortgage_term_node=term,
        )
        mr.push(Money("300000", "GBP"), "test")
        rate.push(Decimal("0.045"), "test")
        term.push(30, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() > Money("0", "GBP")


class TestYearlySinkingFundNode:
    @pytest.mark.asyncio
    async def test_zero_when_no_price(self):
        from houses.nodes.yearly_sinking_fund_node import YearlySinkingFundNode

        price = UserInputNode[Money]("ys_price1", Money)
        rate = UserInputNode("ys_rate1", Decimal)
        node = YearlySinkingFundNode(
            "ys1",
            rightmove_price=price,
            sinking_fund_rate_node=rate,
        )
        price.push(Money("0", "GBP"), "test")
        rate.push(Decimal("0.01"), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("0", "GBP")

    @pytest.mark.asyncio
    async def test_computes_with_price(self):
        from houses.nodes.yearly_sinking_fund_node import YearlySinkingFundNode

        price = UserInputNode[Money]("ys_price2", Money)
        rate = UserInputNode("ys_rate2", Decimal)
        node = YearlySinkingFundNode(
            "ys2",
            rightmove_price=price,
            sinking_fund_rate_node=rate,
        )
        price.push(Money("500000", "GBP"), "test")
        rate.push(Decimal("0.01"), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("5000", "GBP")


class TestTotalMonthlyHousingCostNode:
    @pytest.mark.asyncio
    async def test_returns_zero_when_no_data(self):
        from houses.nodes.total_monthly_housing_cost_node import HousingCostConfig, TotalMonthlyHousingCostNode

        mg = UserInputNode[Money]("tmg", Money)
        sf = UserInputNode[Money]("tsf", Money)
        li = UserInputNode[Money]("tli", Money)
        ri = UserInputNode[Money]("tri", Money)
        st = UserInputNode[str]("tst", str)
        cb = UserInputNode[dict]("tcb", dict)
        ct = UserInputNode[CouncilTaxInfo]("tct", CouncilTaxInfo)
        node = TotalMonthlyHousingCostNode(
            "tmc",
            config=HousingCostConfig(
monthly_mortgage_node=mg,
            yearly_sinking_fund_node=sf,
            life_insurance_node=li,
            rental_income_node=ri,
            status_node=st,
            commute_breakdown_node=cb,
            council_tax_node=ct,
            ),
        )
        mg.push(Money("0", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("0", "GBP"), "test")
        st.push("", "test")
        cb.push({"yearly_total_gbp": "0"}, "test")
        ct.push(CouncilTaxInfo(yearly_cost=Measurement(Money("0", "GBP"), 0.0)), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded, f"node failed: {a.status}: {a.error}"
        total = a.value_or_none()
        assert total is not None
        assert isinstance(total.value, Money)
        assert float(total.value.amount) == 0
        assert total.stddev == 0.0

    @pytest.mark.asyncio
    async def test_computes_total_from_components(self):
        from houses.nodes.total_monthly_housing_cost_node import HousingCostConfig, TotalMonthlyHousingCostNode

        mg = UserInputNode[Money]("tmg2", Money)
        sf = UserInputNode[Money]("tsf2", Money)
        li = UserInputNode[Money]("tli2", Money)
        ri = UserInputNode[Money]("tri2", Money)
        st = UserInputNode[str]("tst2", str)
        cb = UserInputNode[dict]("tcb2", dict)
        ct = UserInputNode[CouncilTaxInfo]("tct2", CouncilTaxInfo)
        node = TotalMonthlyHousingCostNode(
            "tmc2",
            config=HousingCostConfig(
monthly_mortgage_node=mg,
            yearly_sinking_fund_node=sf,
            life_insurance_node=li,
            rental_income_node=ri,
            status_node=st,
            commute_breakdown_node=cb,
            council_tax_node=ct,
            ),
        )
        mg.push(Money("2000", "GBP"), "test")
        sf.push(Money("6000", "GBP"), "test")
        li.push(Money("50", "GBP"), "test")
        ri.push(Money("0", "GBP"), "test")
        st.push("", "test")
        cb.push({"yearly_total_gbp": "1200"}, "test")
        ct.push(CouncilTaxInfo(yearly_cost=Measurement(Money("2400", "GBP"), 0.0)), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        total = a.value_or_none()
        assert total is not None
        # the sinking fund is the FULL monthly share (no ⅔ fudge — every
        # adult is a buyer, so all shares count): 2000 + 500 + 50 + 100 + 200
        expected = 2000 + 500 + 50 + 100 + 200
        assert float(total.value.amount) == pytest.approx(expected, abs=0.01)
        assert total.stddev == 0.0


class TestMonthlySinkingFundProvenance:
    """The monthly sinking fund must be a real node: formula lines and
    data sources, never a raw '7500.0/12*2/3' description with no sources."""

    @pytest.mark.asyncio
    async def test_provenance_has_formula_and_sources(self):
        from houses.nodes.monthly_sinking_fund_node import MonthlySinkingFundNode

        yearly = UserInputNode("msf_yearly", Money)
        yearly.push(Money("7500.00", "GBP"), "test")
        node = MonthlySinkingFundNode("msf_node", yearly_sinking_fund_node=yearly)
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded, f"expected succeeded, got: {a.status}: {a.error}"
        # The ⅔ fudge is gone: monthly = yearly ÷ 12 (the per-person
        # share is applied separately by the headline's group node).
        assert a.value_or_none() == Money("625.00", "GBP")

        prov = await node.build_provenance()
        assert prov.formula is not None, "monthly sinking must expose a formula"
        labels = [line.label for line in prov.formula.lines]
        assert any("Yearly" in lab for lab in labels), labels
        assert any("12" in lab for lab in labels), labels
        assert len(prov.sources) == 1, f"sources must list yearly_sinking_fund, got: {list(prov.sources)}"
