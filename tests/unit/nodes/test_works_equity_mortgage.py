"""Unit tests for TotalWorksNode, EquityTotalNode, MortgageRequiredNode,
and the restructured MonthlyMortgagePaymentNode."""

from __future__ import annotations

from decimal import Decimal

import pytest
from money import Money

from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.model.domain import Person

_ZERO = Decimal("0")


# ── TotalWorksNode ──────────────────────────────────────────────────────


class TestTotalWorksNode:
    @pytest.mark.asyncio
    async def test_required_person_missing(self):
        """Required person not in works dict → impossible."""
        from houses.nodes.total_works_node import TotalWorksNode

        persons = UserInputNode[list]("tw1_ps", list)
        works = UserInputNode[dict]("tw1_ws", dict)
        node = TotalWorksNode("tw1", persons_source=persons, works_estimates_node=works)
        persons.push(
            [
                Person(
                    name="Ashby",
                    has_car=True,
                    works_estimate_required=True,
                )
            ],
            "test",
        )
        # Both deps must be resolved for compute to run
        works.push({}, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.impossible
        assert "Ashby" in a.error

    @pytest.mark.asyncio
    async def test_no_one_requires(self):
        """No required persons → succeeded(0) even with empty dict."""
        from houses.nodes.total_works_node import TotalWorksNode

        persons = UserInputNode[list]("tw2_ps", list)
        works = UserInputNode[dict]("tw2_ws", dict)
        node = TotalWorksNode("tw2", persons_source=persons, works_estimates_node=works)
        persons.push(
            [
                Person(
                    name="Ashby",
                    has_car=True,
                    works_estimate_required=False,
                )
            ],
            "test",
        )
        works.push({}, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("0", "GBP")

    @pytest.mark.asyncio
    async def test_empty_dict_with_values(self):
        """Dict with values sums correctly."""
        from houses.nodes.total_works_node import TotalWorksNode

        persons = UserInputNode[list]("tw3_ps", list)
        works = UserInputNode[dict]("tw3_ws", dict)
        node = TotalWorksNode("tw3", persons_source=persons, works_estimates_node=works)
        persons.push(
            [
                Person(
                    name="Simon",
                    has_car=True,
                    works_estimate_required=False,
                ),
                Person(
                    name="Ashby",
                    has_car=True,
                    works_estimate_required=False,
                ),
            ],
            "test",
        )
        works.push({"Ashby": 20000, "Simon": 5000}, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("25000", "GBP")

    @pytest.mark.asyncio
    async def test_some_required_missing(self):
        """Only some required persons missing → error lists those."""
        from houses.nodes.total_works_node import TotalWorksNode

        persons = UserInputNode[list]("tw4_ps", list)
        works = UserInputNode[dict]("tw4_ws", dict)
        node = TotalWorksNode("tw4", persons_source=persons, works_estimates_node=works)
        persons.push(
            [
                Person(
                    name="Simon",
                    has_car=True,
                    works_estimate_required=True,
                ),
                Person(
                    name="Ashby",
                    has_car=True,
                    works_estimate_required=False,
                ),
            ],
            "test",
        )
        works.push({"Ashby": 5000}, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.impossible
        assert "Simon" in a.error
        assert "Ashby" not in a.error

    @pytest.mark.asyncio
    async def test_zeros_in_dict(self):
        """Zeros in dict → succeeded(0)."""
        from houses.nodes.total_works_node import TotalWorksNode

        persons = UserInputNode[list]("tw5_ps", list)
        works = UserInputNode[dict]("tw5_ws", dict)
        node = TotalWorksNode("tw5", persons_source=persons, works_estimates_node=works)
        persons.push(
            [
                Person(
                    name="Ashby",
                    has_car=True,
                    works_estimate_required=False,
                )
            ],
            "test",
        )
        works.push({"Ashby": 0}, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("0", "GBP")


# ── EquityTotalNode ─────────────────────────────────────────────────────


class TestEquityTotalNode:
    @pytest.mark.asyncio
    async def test_single_person_home_sale_only(self):
        from houses.nodes.equity_total_node import EquityTotalNode

        persons = UserInputNode[list]("eq1_ps", list)
        node = EquityTotalNode("eq1", persons_source=persons)
        persons.push(
            [
                Person(
                    name="Simon",
                    has_car=True,
                    home_sale_price=Money("500000", "GBP"),
                )
            ],
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("500000", "GBP")

    @pytest.mark.asyncio
    async def test_tolerates_plain_dict_person_entries(self):
        """A legacy dict person entry must fall back to the cash path, not
        crash the equity node (which would freeze the mortgage cascade)."""
        from houses.nodes.equity_total_node import EquityTotalNode

        persons = UserInputNode[list]("eq5_ps", list)
        node = EquityTotalNode("eq5", persons_source=persons)
        persons.push(
            [
                {
                    "name": "Legacy",
                    "home_sale_price": Money("500000", "GBP"),
                    "outstanding_mortgage": Money("300000", "GBP"),
                    "cash_contribution": Money("100000", "GBP"),
                }
            ],
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        # the node must NOT crash (a crash would freeze the mortgage
        # cascade); dict values are unreachable via getattr (pre-existing
        # semantics) so a dict entry contributes 0
        assert a.succeeded
        assert a.value_or_none() == Money("0", "GBP")

    @pytest.mark.asyncio
    async def test_home_equity_excluded_when_not_selling(self):
        """A person NOT selling a home contributes cash only — stale home
        fields must not leak into equity (the Ashby shape)."""
        from houses.nodes.equity_total_node import EquityTotalNode

        persons = UserInputNode[list]("eq3_ps", list)
        node = EquityTotalNode("eq3", persons_source=persons)
        persons.push(
            [
                Person(
                    name="Ashby",
                    has_car=True,
                    home_sale_price=Money("500000", "GBP"),
                    outstanding_mortgage=Money("300000", "GBP"),
                    cash_contribution=Money("100000", "GBP"),
                    selling_home=False,
                )
            ],
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("100000", "GBP")

    @pytest.mark.asyncio
    async def test_home_equity_inferred_when_selling(self):
        """Home equity counts when selling_home is unset but home values
        exist (the migration inference)."""
        from houses.nodes.equity_total_node import EquityTotalNode

        persons = UserInputNode[list]("eq4_ps", list)
        node = EquityTotalNode("eq4", persons_source=persons)
        persons.push(
            [
                Person(
                    name="Simon",
                    has_car=True,
                    home_sale_price=Money("500000", "GBP"),
                    outstanding_mortgage=Money("300000", "GBP"),
                    cash_contribution=Money("100000", "GBP"),
                )
            ],
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("300000", "GBP")

    @pytest.mark.asyncio
    async def test_single_person_cash_only(self):
        from houses.nodes.equity_total_node import EquityTotalNode

        persons = UserInputNode[list]("eq2_ps", list)
        node = EquityTotalNode("eq2", persons_source=persons)
        persons.push(
            [
                Person(
                    name="Ashby",
                    has_car=True,
                    cash_contribution=Money("100000", "GBP"),
                )
            ],
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("100000", "GBP")

    @pytest.mark.asyncio
    async def test_home_with_equity_via_partial_mortgage(self):
        from houses.nodes.equity_total_node import EquityTotalNode

        persons = UserInputNode[list]("eq3_ps", list)
        node = EquityTotalNode("eq3", persons_source=persons)
        persons.push(
            [
                Person(
                    name="Simon",
                    has_car=True,
                    home_sale_price=Money("500000", "GBP"),
                    outstanding_mortgage=Money("200000", "GBP"),
                )
            ],
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("300000", "GBP")

    @pytest.mark.asyncio
    async def test_negative_equity_floored_at_zero(self):
        from houses.nodes.equity_total_node import EquityTotalNode

        persons = UserInputNode[list]("eq4_ps", list)
        node = EquityTotalNode("eq4", persons_source=persons)
        persons.push(
            [
                Person(
                    name="Simon",
                    has_car=True,
                    home_sale_price=Money("200000", "GBP"),
                    outstanding_mortgage=Money("300000", "GBP"),
                )
            ],
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("0", "GBP")

    @pytest.mark.asyncio
    async def test_both_home_and_cash_on_same_person(self):
        from houses.nodes.equity_total_node import EquityTotalNode

        persons = UserInputNode[list]("eq5_ps", list)
        node = EquityTotalNode("eq5", persons_source=persons)
        persons.push(
            [
                Person(
                    name="Ashby",
                    has_car=True,
                    home_sale_price=Money("200000", "GBP"),
                    cash_contribution=Money("50000", "GBP"),
                )
            ],
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("250000", "GBP")

    @pytest.mark.asyncio
    async def test_cross_person_sum(self):
        from houses.nodes.equity_total_node import EquityTotalNode

        persons = UserInputNode[list]("eq6_ps", list)
        node = EquityTotalNode("eq6", persons_source=persons)
        persons.push(
            [
                Person(
                    name="Simon",
                    has_car=True,
                    home_sale_price=Money("300000", "GBP"),
                    outstanding_mortgage=Money("100000", "GBP"),
                ),
                Person(
                    name="Ashby",
                    has_car=True,
                    cash_contribution=Money("200000", "GBP"),
                ),
            ],
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("400000", "GBP")

    @pytest.mark.asyncio
    async def test_all_defaults(self):
        from houses.nodes.equity_total_node import EquityTotalNode

        persons = UserInputNode[list]("eq7_ps", list)
        node = EquityTotalNode("eq7", persons_source=persons)
        p = Person(name="Nobody", has_car=True)
        persons.push([p], "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("0", "GBP")


# ── MortgageRequiredNode ────────────────────────────────────────────────


class TestMortgageRequiredNode:
    @pytest.mark.asyncio
    async def test_standard(self):
        from houses.nodes.mortgage_required_node import MortgageRequiredNode

        price = UserInputNode[Money]("mr1_p", Money)
        sd = UserInputNode[Money]("mr1_sd", Money)
        tw = UserInputNode[Money]("mr1_tw", Money)
        te = UserInputNode[Money]("mr1_te", Money)
        node = MortgageRequiredNode(
            "mr1",
            rightmove_price=price,
            stamp_duty=sd,
            total_works_node=tw,
            total_equity_node=te,
        )
        price.push(Money("500000", "GBP"), "test")
        sd.push(Money("15000", "GBP"), "test")
        tw.push(Money("20000", "GBP"), "test")
        te.push(Money("477000", "GBP"), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("58000", "GBP")

    @pytest.mark.asyncio
    async def test_no_works(self):
        from houses.nodes.mortgage_required_node import MortgageRequiredNode

        price = UserInputNode[Money]("mr2_p", Money)
        sd = UserInputNode[Money]("mr2_sd", Money)
        tw = UserInputNode[Money]("mr2_tw", Money)
        te = UserInputNode[Money]("mr2_te", Money)
        node = MortgageRequiredNode(
            "mr2",
            rightmove_price=price,
            stamp_duty=sd,
            total_works_node=tw,
            total_equity_node=te,
        )
        price.push(Money("500000", "GBP"), "test")
        sd.push(Money("15000", "GBP"), "test")
        tw.push(Money("0", "GBP"), "test")
        te.push(Money("477000", "GBP"), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("38000", "GBP")

    @pytest.mark.asyncio
    async def test_no_equity(self):
        from houses.nodes.mortgage_required_node import MortgageRequiredNode

        price = UserInputNode[Money]("mr3_p", Money)
        sd = UserInputNode[Money]("mr3_sd", Money)
        tw = UserInputNode[Money]("mr3_tw", Money)
        te = UserInputNode[Money]("mr3_te", Money)
        node = MortgageRequiredNode(
            "mr3",
            rightmove_price=price,
            stamp_duty=sd,
            total_works_node=tw,
            total_equity_node=te,
        )
        price.push(Money("500000", "GBP"), "test")
        sd.push(Money("15000", "GBP"), "test")
        tw.push(Money("20000", "GBP"), "test")
        te.push(Money("0", "GBP"), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("535000", "GBP")

    @pytest.mark.asyncio
    async def test_all_zeros(self):
        from houses.nodes.mortgage_required_node import MortgageRequiredNode

        price = UserInputNode[Money]("mr4_p", Money)
        sd = UserInputNode[Money]("mr4_sd", Money)
        tw = UserInputNode[Money]("mr4_tw", Money)
        te = UserInputNode[Money]("mr4_te", Money)
        node = MortgageRequiredNode(
            "mr4",
            rightmove_price=price,
            stamp_duty=sd,
            total_works_node=tw,
            total_equity_node=te,
        )
        price.push(Money("0", "GBP"), "test")
        sd.push(Money("0", "GBP"), "test")
        tw.push(Money("0", "GBP"), "test")
        te.push(Money("0", "GBP"), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("0", "GBP")

    @pytest.mark.asyncio
    async def test_impossible_from_works_node(self):
        """When total_works is impossible, mortgage_required reflects it."""
        from houses.nodes.mortgage_required_node import MortgageRequiredNode
        from houses.nodes.total_works_node import TotalWorksNode

        price = UserInputNode[Money]("mr5_p", Money)
        sd = UserInputNode[Money]("mr5_sd", Money)
        persons = UserInputNode[list]("mr5_ps", list)
        works = UserInputNode[dict]("mr5_ws", dict)
        tw_node = TotalWorksNode(
            "mr5_tw",
            persons_source=persons,
            works_estimates_node=works,
        )
        te = UserInputNode[Money]("mr5_te", Money)

        node = MortgageRequiredNode(
            "mr5",
            rightmove_price=price,
            stamp_duty=sd,
            total_works_node=tw_node,
            total_equity_node=te,
        )

        persons.push(
            [
                Person(
                    name="Ashby",
                    has_car=True,
                    works_estimate_required=True,
                )
            ],
            "test",
        )
        works.push({}, "test")  # empty → required person missing
        price.push(Money("500000", "GBP"), "test")
        sd.push(Money("15000", "GBP"), "test")
        te.push(Money("477000", "GBP"), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.impossible
        assert "Ashby" in a.error


# ── MonthlyMortgagePaymentNode (restructured) ──────────────────────────


class TestMonthlyMortgagePaymentNode:
    @pytest.mark.asyncio
    async def test_zero_when_no_principal(self):
        from houses.nodes.monthly_mortgage_payment_node import (
            MonthlyMortgagePaymentNode,
        )

        mr = UserInputNode[Money]("mmp1_mr", Money)
        rate = UserInputNode("mmp1_rate", Decimal)
        term = UserInputNode("mmp1_term", int)
        node = MonthlyMortgagePaymentNode(
            "mmp1", mortgage_required_node=mr, mortgage_rate_node=rate, mortgage_term_node=term
        )
        mr.push(Money("0", "GBP"), "test")
        rate.push(Decimal("0.045"), "test")
        term.push(30, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == Money("0", "GBP")

    @pytest.mark.asyncio
    async def test_standard_pmt(self):
        from houses.nodes.monthly_mortgage_payment_node import (
            MonthlyMortgagePaymentNode,
        )

        mr = UserInputNode[Money]("mmp2_mr", Money)
        rate = UserInputNode("mmp2_rate", Decimal)
        term = UserInputNode("mmp2_term", int)
        node = MonthlyMortgagePaymentNode(
            "mmp2", mortgage_required_node=mr, mortgage_rate_node=rate, mortgage_term_node=term
        )
        mr.push(Money("235000", "GBP"), "test")
        rate.push(Decimal("0.0495"), "test")
        term.push(27, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        actual = a.value_or_none().amount
        assert actual > _ZERO

    @pytest.mark.asyncio
    async def test_no_persons_source_dep(self):
        """No persons_source dependency."""
        from houses.nodes.monthly_mortgage_payment_node import (
            MonthlyMortgagePaymentNode,
        )

        mr = UserInputNode[Money]("mmp3_mr", Money)
        rate = UserInputNode("mmp3_rate", Decimal)
        term = UserInputNode("mmp3_term", int)
        node = MonthlyMortgagePaymentNode(
            "mmp3", mortgage_required_node=mr, mortgage_rate_node=rate, mortgage_term_node=term
        )
        deps = node._get_active_deps()
        dep_ids = {d._id for d in deps}
        assert "persons" not in dep_ids

    @pytest.mark.asyncio
    async def test_impossible_propagates(self):
        """When mortgage_required is impossible, monthly reflects it."""
        from houses.nodes.monthly_mortgage_payment_node import (
            MonthlyMortgagePaymentNode,
        )
        from houses.nodes.mortgage_required_node import (
            MortgageRequiredNode,
        )
        from houses.nodes.total_works_node import TotalWorksNode

        price = UserInputNode[Money]("mmp4_p", Money)
        sd = UserInputNode[Money]("mmp4_sd", Money)
        persons = UserInputNode[list]("mmp4_ps", list)
        works = UserInputNode[dict]("mmp4_ws", dict)
        tw_node = TotalWorksNode(
            "mmp4_tw",
            persons_source=persons,
            works_estimates_node=works,
        )
        te = UserInputNode[Money]("mmp4_te", Money)
        mr_node = MortgageRequiredNode(
            "mmp4_mr",
            rightmove_price=price,
            stamp_duty=sd,
            total_works_node=tw_node,
            total_equity_node=te,
        )
        rate = UserInputNode("mmp4_rate", Decimal)
        term = UserInputNode("mmp4_term", int)
        node = MonthlyMortgagePaymentNode(
            "mmp4",
            mortgage_required_node=mr_node,
            mortgage_rate_node=rate,
            mortgage_term_node=term,
        )

        persons.push(
            [
                Person(
                    name="Ashby",
                    has_car=True,
                    works_estimate_required=True,
                )
            ],
            "test",
        )
        works.push({}, "test")
        price.push(Money("500000", "GBP"), "test")
        sd.push(Money("15000", "GBP"), "test")
        te.push(Money("477000", "GBP"), "test")
        rate.push(Decimal("0.0495"), "test")
        term.push(27, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.impossible
        assert "Ashby" in a.error


# ── TotalMonthlyHousingCostNode (impossible propagation) ──────────────


class TestTotalMonthlyHousingCostImpossible:
    @pytest.mark.asyncio
    async def test_all_sources_succeed(self):
        from houses.council_tax_info import CouncilTaxInfo
        from houses.nodes.total_monthly_housing_cost_node import (
            TotalMonthlyHousingCostNode,
        )

        mg = UserInputNode[Money]("tm_ok_mg", Money)
        sf = UserInputNode[Money]("tm_ok_sf", Money)
        li = UserInputNode[Money]("li_ok", Money)
        ri = UserInputNode[Money]("ri_ok", Money)
        st = UserInputNode[str]("st_ok", str)
        fin = UserInputNode[dict]("tm_ok_fin", dict)
        cb = UserInputNode[dict]("tm_ok_cb", dict)
        ct = UserInputNode[CouncilTaxInfo]("tm_ok_ct", CouncilTaxInfo)
        node = TotalMonthlyHousingCostNode(
            "tm_ok",
            monthly_mortgage_node=mg,
            yearly_sinking_fund_node=sf,
            life_insurance_node=li,
            rental_income_node=ri,
            status_node=st,
            commute_breakdown_node=cb,
            council_tax_node=ct,
        )

        mg.push(Money("1000", "GBP"), "test")
        sf.push(Money("1200", "GBP"), "test")
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("0", "GBP"), "test")
        st.push("", "test")
        fin.push({}, "test")
        cb.push({}, "test")
        ct.push(CouncilTaxInfo(), "test")
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
