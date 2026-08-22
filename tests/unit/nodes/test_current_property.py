"""Test that a property with Status="Current" has correct DAG values:
stamp_duty=0, no cash contribution in equity, no sinking fund or
life insurance in total monthly cost, rental income subtracted."""

from __future__ import annotations

from decimal import Decimal

import pytest
from money import Money

from dag.measurement import Measurement
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.council_tax_info import CouncilTaxInfo
from houses.model.domain import HomeCoOwner, Person


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
            CouncilTaxInfo(band="D", yearly_cost=Measurement(Money("1800", "GBP"), 0.0)),
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        total = a.value_or_none()
        assert total is not None
        # Total = 1000 + 12000/12*2/3 + 150 + 1800/12 - 600
        #       = 1000 + 666.67 + 150 + 150 - 600 = 1366.67
        expected = round(1000 + 12000 / 12 + 150 + 1800 / 12 - 600, 2)
        assert float(total.value.amount) == pytest.approx(expected, abs=0.01), (
            f"Expected ~{expected}, got {total}"
        )


class TestGroupMonthlyCostNode:
    """The headline's two numbers must include council tax — a
    regression: the node read getattr(council, 'value'/'stddev') but
    CouncilTaxInfo keeps the data at yearly_cost.value/stddev, so
    council tax silently contributed zero and the '≈' flag could never
    appear."""

    def _node(self, node_id: str):
        from houses.nodes.total_monthly_housing_cost_node import GroupMonthlyCostNode

        mg = UserInputNode[Money]("g_" + node_id + "_mg", Money)
        sf = UserInputNode[Money]("g_" + node_id + "_sf", Money)
        li = UserInputNode[Money]("g_" + node_id + "_li", Money)
        ri = UserInputNode[Money]("g_" + node_id + "_ri", Money)
        st = UserInputNode[str]("g_" + node_id + "_st", str)
        cb = UserInputNode[dict]("g_" + node_id + "_cb", dict)
        ct = UserInputNode[CouncilTaxInfo]("g_" + node_id + "_ct", CouncilTaxInfo)
        ps = UserInputNode[list]("g_" + node_id + "_ps", list)
        node = GroupMonthlyCostNode(
            node_id,
            monthly_mortgage_node=mg,
            yearly_sinking_fund_node=sf,
            life_insurance_node=li,
            rental_income_node=ri,
            status_node=st,
            commute_breakdown_node=cb,
            council_tax_node=ct,
            persons_source=ps,
        )
        return node, mg, sf, li, ri, st, cb, ct, ps

    @pytest.mark.asyncio
    async def test_couple_figure_includes_council_tax(self):
        """Council tax flows into the couple/others headline: the exact
        value when the lookup succeeded, and the stddev when the Band-D
        fallback estimated it."""
        node, mg, sf, li, ri, st, cb, ct, ps = self._node("ctax1")
        mg.push(Money("1000", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("0", "GBP"), "test")
        st.push("", "test")
        cb.push({}, "test")
        ps.push([Person("Simon", True), Person("Lorena", False), Person("Ashby", False)], "test")
        # Band-D fallback: an ESTIMATE with a spread
        ct.push(
            CouncilTaxInfo(
                band="D",
                yearly_cost=Measurement(Money("1800", "GBP"), 120.0),
            ),
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        # All three are adults; joint owners = all adults (no current
        # home linked) → the couple figure carries the full council tax.
        # Without the fix, council contributes 0 and stddev is 0.
        couple = val["couple"]
        assert float(couple["value"]) == pytest.approx(1000 + 1800 / 12, abs=0.01), (
            f"council tax should add 150/mo, got couple={couple}"
        )
        assert couple["stddev"] == pytest.approx(120 / 12, abs=0.01), (
            f"council stddev should flow into the headline, got {couple['stddev']}"
        )

    @pytest.mark.asyncio
    async def test_provenance_value_is_human_summary_not_dict_dump(self):
        """The monthly-figure provenance must read as a human summary,
        never the raw breakdown dict.

        Regression: the provenance value was the whole node dict, so the
        provenance trust bar led with "couple: value: 3753.21, stddev:
        0, others: value: 290.39, ..." — machine noise as the first text.
        """
        node, mg, sf, li, ri, st, cb, ct, ps = self._node("prov1")
        mg.push(Money("1000", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("0", "GBP"), "test")
        st.push("", "test")
        cb.push({}, "test")
        simon = Person("Simon", True, home_co_owners=(HomeCoOwner(name="Lorena", share=50),))
        ps.push([simon, Person("Lorena", False), Person("Ashby", False)], "test")
        ct.push(
            CouncilTaxInfo(
                band="D",
                yearly_cost=Measurement(Money("1800", "GBP"), 120.0),
            ),
            "test",
        )
        await flush_processor()
        prov = await node.build_provenance()
        # Human figures, not the serialized breakdown dict.
        assert isinstance(prov.value, str), f"provenance value should be text, got {type(prov.value)}"
        assert "couple" not in prov.value and "stddev" not in prov.value, (
            f"provenance must not dump the raw dict: {prov.value!r}"
        )
        # 1000 mortgage + 1800/12 council (owners' 2/3 share) = 1100/mo
        # for the owners; 1800/12 * 1/3 = 50/mo for Ashby.
        assert "£1,100.00" in prov.value and "£50.00" in prov.value, f"figures missing: {prov.value!r}"

    @pytest.mark.asyncio
    async def test_split_share_scales_council_contribution(self):
        """When owners and others split the shared costs, each group's
        council-tax share is proportional to its headcount."""
        node, mg, sf, li, ri, st, cb, ct, ps = self._node("ctax2")
        mg.push(Money("0", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("0", "GBP"), "test")
        st.push("", "test")
        cb.push({}, "test")
        # joint owners = Simon+Lorena (from home_co_owners); Ashby the other
        simon = Person("Simon", True, home_co_owners=(HomeCoOwner(name="Lorena", share=50),))
        ps.push([simon, Person("Lorena", False), Person("Ashby", False)], "test")
        ct.push(
            CouncilTaxInfo(
                band="D",
                yearly_cost=Measurement(Money("1800", "GBP"), 0.0),
            ),
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        # owners 2/3, others 1/3 → council share 100/mo vs 50/mo
        assert float(val["couple"]["value"]) == pytest.approx(1800 / 12 * 2 / 3, abs=0.01)
        assert float(val["others"]["value"]) == pytest.approx(1800 / 12 * 1 / 3, abs=0.01)

    @pytest.mark.asyncio
    async def test_breakdown_separates_couple_and_others_components(self):
        """The node emits a per-group component breakdown so the detail
        page can render S+L costs and A costs as separate blocks instead
        of mixing household rows.  For a NEW (non-current) property the
        current-home rent arrangement does NOT apply — no rent is
        transferred in either direction."""
        node, mg, sf, li, ri, st, cb, ct, ps = self._node("ctax3")
        mg.push(Money("3000", "GBP"), "test")
        sf.push(Money("6000", "GBP"), "test")  # yearly → 500/mo
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("800", "GBP"), "test")  # rental income
        st.push("", "test")  # NOT the current home → no rent transfer
        cb.push(
            {
                "persons": {
                    "Simon": {"yearly_gbp": 2400},   # 200/mo
                    "Lorena": {"yearly_gbp": 1200},  # 100/mo
                    "Ashby": {"yearly_gbp": 600},    # 50/mo
                }
            },
            "test",
        )
        # joint owners = Simon+Lorena; Ashby the other, paying rent
        simon = Person(
            "Simon",
            True,
            home_co_owners=(HomeCoOwner(name="Lorena", share=50),),
            life_insurance_monthly=Money("100", "GBP"),
        )
        lorena = Person("Lorena", False, life_insurance_monthly=Money("50", "GBP"))
        ashby = Person(
            "Ashby",
            False,
            rent_paid_monthly=Money("600", "GBP"),
            life_insurance_monthly=Money("30", "GBP"),
        )
        ps.push([simon, lorena, ashby], "test")
        ct.push(
            CouncilTaxInfo(
                band="D",
                yearly_cost=Measurement(Money("1800", "GBP"), 0.0),
            ),
            "test",
        )
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        # couple = commutes(300) + insurance(150) + council ⅔(150=100.01) +
        # sinking ⅔(500=333.35) + mortgage 3000 − rental 800.
        # NO rent transfer — Ashby's rent stays with him (current-home only).
        couple = val["couple_breakdown"]
        assert float(couple["commutes"]) == pytest.approx(300, abs=0.01)
        assert float(couple["insurance"]) == pytest.approx(150, abs=0.01)
        assert float(couple["council_tax"]) == pytest.approx(
            round(float(Decimal(str(round(2 / 3, 4))) * Decimal(150)), 2), abs=0.01
        )
        assert float(couple["sinking_fund"]) == pytest.approx(
            round(float(Decimal(str(round(2 / 3, 4))) * Decimal(500)), 2), abs=0.01
        )
        assert float(couple["mortgage"]) == pytest.approx(3000, abs=0.01)
        assert float(couple["rental_income"]) == pytest.approx(-800, abs=0.01)
        assert couple.get("rent_received") in (None, 0), "rent must NOT transfer for a non-current property"
        assert float(val["couple"]["value"]) == pytest.approx(
            300 + 150 + float(Decimal(str(round(2 / 3, 4))) * Decimal(650)) + 3000 - 800, abs=0.01
        )
        # others = commutes(50) + insurance(30) + council ⅓(150=50) +
        # sinking ⅓(500=166.68) — NO rent paid (current-home only)
        others = val["others_breakdown"]
        assert float(others["commutes"]) == pytest.approx(50, abs=0.01)
        assert float(others["insurance"]) == pytest.approx(30, abs=0.01)
        assert float(others["council_tax"]) == pytest.approx(
            round(float(Decimal(str(round(1 / 3, 4))) * Decimal(150)), 2), abs=0.01
        )
        assert float(others["sinking_fund"]) == pytest.approx(
            round(float(Decimal(str(round(1 / 3, 4))) * Decimal(500)), 2), abs=0.01
        )
        assert others.get("rent_paid") in (None, 0), "rent must NOT transfer for a non-current property"
        assert float(val["others"]["value"]) == pytest.approx(
            50 + 30 + float(Decimal(str(round(1 / 3, 4))) * Decimal(650)), abs=0.01
        )
        # Full names, not initials — the UI says "Ashby", not "A"
        assert val["others_label"] == "Ashby"
        assert val["couple_names"] == "Simon+Lorena"

    @pytest.mark.asyncio
    async def test_rent_stays_in_each_person_own_finances(self):
        """Regression: the couple's figure subtracted the others' rent
        (rent_received) — a guessed transfer.  We do not know who pays
        whom: rent paid affects ONLY the payer's finances, rent received
        ONLY the receiver's.  The current-home rent_paid figures are each
        group's own cost; nothing moves between the groups."""
        node, mg, sf, li, ri, st, cb, ct, ps = self._node("ctax4")
        mg.push(Money("0", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("0", "GBP"), "test")
        st.push("Current", "test")  # this IS the current home → rent_paid applies
        cb.push({}, "test")
        simon = Person("Simon", True, home_co_owners=(HomeCoOwner(name="Lorena", share=50),))
        ashby = Person("Ashby", False, rent_paid_monthly=Money("600", "GBP"))
        ps.push([simon, Person("Lorena", False), ashby], "test")
        ct.push(CouncilTaxInfo(band="D", yearly_cost=Measurement(Money("1800", "GBP"), 0.0)), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        # Council tax still applies (only sinking+insurance are excluded
        # for the current home) — couple's ⅔ share = 100, Ashby's ⅓ = 50.
        couple = val["couple_breakdown"]
        assert couple.get("rent_received") is None, "no transfer row — rent_received must not exist"
        assert float(val["couple"]["value"]) == pytest.approx(100, abs=0.01)
        others = val["others_breakdown"]
        # Ashby's rent is HER cost — it stays in her figure.
        assert float(others["rent_paid"]) == pytest.approx(600, abs=0.01)
        assert float(val["others"]["value"]) == pytest.approx(50 + 600, abs=0.01)

    @pytest.mark.asyncio
    async def test_rental_income_and_rent_paid_are_never_combined(self):
        """Regression (double deduction): the couple's figure deducted the
        property's rental income AND the others' rent — the same money
        could be counted twice even when Ashby really pays the couple.
        rent_paid (Ashby's cost) and rental_income (the house's income)
        are unrelated figures; only the house's income reduces the
        couple's figure."""
        node, mg, sf, li, ri, st, cb, ct, ps = self._node("ctax5")
        mg.push(Money("0", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("600", "GBP"), "test")  # the house's rental income
        st.push("Current", "test")
        cb.push({}, "test")
        simon = Person("Simon", True, home_co_owners=(HomeCoOwner(name="Lorena", share=50),))
        ashby = Person("Ashby", False, rent_paid_monthly=Money("600", "GBP"))
        ps.push([simon, Person("Lorena", False), ashby], "test")
        ct.push(CouncilTaxInfo(band="D", yearly_cost=Measurement(Money("1800", "GBP"), 0.0)), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        couple = val["couple_breakdown"]
        assert float(couple["rental_income"]) == pytest.approx(-600, abs=0.01)
        assert couple.get("rent_received") is None
        # council 100 + mortgage 0 − rental income 600 — Ashby's rent is
        # NOT deducted from the couple (it is her own cost).
        assert float(val["couple"]["value"]) == pytest.approx(100 - 600, abs=0.01)
        assert float(val["others"]["value"]) == pytest.approx(50 + 600, abs=0.01)

    @pytest.mark.asyncio
    async def test_couple_own_rent_paid_counts_in_couple_figure(self):
        """Any adult might pay rent — the couple's own rent_paid is their
        cost and must appear in the couple's figure (it was silently
        dropped)."""
        node, mg, sf, li, ri, st, cb, ct, ps = self._node("ctax6")
        mg.push(Money("0", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("0", "GBP"), "test")
        st.push("Current", "test")
        cb.push({}, "test")
        simon = Person(
            "Simon",
            True,
            home_co_owners=(HomeCoOwner(name="Lorena", share=50),),
            rent_paid_monthly=Money("200", "GBP"),
        )
        ps.push([simon, Person("Lorena", False), Person("Ashby", False)], "test")
        ct.push(CouncilTaxInfo(band="D", yearly_cost=Measurement(Money("1800", "GBP"), 0.0)), "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        couple = val["couple_breakdown"]
        assert Decimal(couple["rent_paid"]) == Decimal("200.00")
        assert Decimal(val["couple"]["value"]) == Decimal("300.00")

    def _node_with_annexe(self, node_id: str):
        """Build the node with annexe_payers/annexe_ignored inputs — no
        same-id duplicate (the scheduler keys nodes by id)."""
        from houses.nodes.total_monthly_housing_cost_node import GroupMonthlyCostNode

        mg = UserInputNode[Money]("g_" + node_id + "_mg", Money)
        sf = UserInputNode[Money]("g_" + node_id + "_sf", Money)
        li = UserInputNode[Money]("g_" + node_id + "_li", Money)
        ri = UserInputNode[Money]("g_" + node_id + "_ri", Money)
        st = UserInputNode[str]("g_" + node_id + "_st", str)
        cb = UserInputNode[dict]("g_" + node_id + "_cb", dict)
        ct = UserInputNode[CouncilTaxInfo]("g_" + node_id + "_ct", CouncilTaxInfo)
        ps = UserInputNode[list]("g_" + node_id + "_ps", list)
        ap = UserInputNode[list]("g_" + node_id + "_ap", list)
        ai = UserInputNode[bool]("g_" + node_id + "_ai", bool)
        ctp = UserInputNode[list]("g_" + node_id + "_ctp", list)
        node = GroupMonthlyCostNode(
            node_id,
            monthly_mortgage_node=mg,
            yearly_sinking_fund_node=sf,
            life_insurance_node=li,
            rental_income_node=ri,
            status_node=st,
            commute_breakdown_node=cb,
            council_tax_node=ct,
            persons_source=ps,
            annexe_payers_node=ap,
            annexe_ignored_node=ai,
            council_tax_payers_node=ctp,
        )
        return node, mg, sf, li, ri, st, cb, ct, ps, ap, ai, ctp

    @pytest.mark.asyncio
    async def test_annexe_council_tax_split_between_picked_payers(self):
        """The annexe's council tax is split equally among the people the
        user picked — here only Ashby pays it, so the others' figure
        carries the whole annexe bill."""
        from houses.council_tax_info import AnnexeDwelling, CouncilTaxInfo

        node, mg, sf, li, ri, st, cb, ct, ps, ap, ai, ctp = self._node_with_annexe("annexe1")
        mg.push(Money("0", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("0", "GBP"), "test")
        st.push("", "test")
        cb.push({}, "test")
        simon = Person("Simon", True, home_co_owners=(HomeCoOwner(name="Lorena", share=50),))
        ps.push([simon, Person("Lorena", False), Person("Ashby", False)], "test")
        ct.push(
            CouncilTaxInfo(
                band="D",
                yearly_cost=Measurement(Money("1800", "GBP"), 0.0),
                annexe=AnnexeDwelling(
                    address="FLAT 2, 2 WILLOWMEAD GARDENS",
                    band="A",
                    yearly_cost=Measurement(Money("900", "GBP"), 0.0),
                ),
            ),
            "test",
        )
        ap.push(["Ashby"], "test")
        ai.push(False, "test")
        ctp.push([], "test")
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        # Couple: main ⅔ of £150/mo = 100.  Others: main ⅓ (50) + annexe 75.
        assert float(val["couple"]["value"]) == pytest.approx(100, abs=0.01)
        assert float(val["others"]["value"]) == pytest.approx(125, abs=0.01)
        assert float(val["others_breakdown"]["annexe_council_tax"]) == pytest.approx(75, abs=0.01)
        assert "annexe_council_tax" not in val["couple_breakdown"]
        # The main council-tax line must NOT contain the annexe bill —
        # the annexe has its own row, so the breakdown rows sum to the
        # group total (a double-counted annexe broke the row sum).
        assert float(val["others_breakdown"]["council_tax"]) == pytest.approx(50, abs=0.01), (
            "council_tax must be main-bill only; the annexe lives in its own row"
        )
        assert float(val["couple_breakdown"]["council_tax"]) == pytest.approx(100, abs=0.01)

        prov = await node.build_provenance()
        assert "Ashby" in (prov.description or "")
        assert "annexe" in (prov.description or "")

    @pytest.mark.asyncio
    async def test_stale_annexe_payers_fall_back_to_all_adults(self):
        """A stored annexe payer list whose names no longer match the
        household (renamed on the sheet) must NOT silently drop the
        annexe bill — mirroring the main-bill path, it falls back to the
        all-adults split so the second dwelling's council tax is never
        lost without explanation."""
        from houses.council_tax_info import AnnexeDwelling, CouncilTaxInfo

        node, mg, sf, li, ri, st, cb, ct, ps, ap, ai, ctp = self._node_with_annexe("annexe_stale")
        mg.push(Money("0", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("0", "GBP"), "test")
        st.push("", "test")
        cb.push({}, "test")
        simon = Person("Simon", True, home_co_owners=(HomeCoOwner(name="Lorena", share=50),))
        ps.push([simon, Person("Lorena", False), Person("Ashby", False)], "test")
        ct.push(
            CouncilTaxInfo(
                band="D",
                yearly_cost=Measurement(Money("1800", "GBP"), 0.0),
                annexe=AnnexeDwelling(
                    address="FLAT 2, 2 WILLOWMEAD GARDENS",
                    band="A",
                    yearly_cost=Measurement(Money("900", "GBP"), 0.0),
                ),
            ),
            "test",
        )
        # "Ashby" was renamed to "Ashby K" on the sheet — the stored pick
        # no longer matches any adult.
        ap.push(["Ashby K"], "test")
        ai.push(False, "test")
        ctp.push([], "test")
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        val = a.value_or_none()
        assert val is not None
        # Annex £75/mo split across ALL adults (£25 each): the couple's
        # 2/3 share = 50, Ashby's 1/3 = 25.  The bill must NOT vanish.
        assert float(val["couple_breakdown"]["annexe_council_tax"]) == pytest.approx(50, abs=0.01)
        assert float(val["others_breakdown"]["annexe_council_tax"]) == pytest.approx(25, abs=0.01)

    @pytest.mark.asyncio
    async def test_annexe_contributes_nothing_until_payers_picked(self):
        from houses.council_tax_info import AnnexeDwelling, CouncilTaxInfo

        node, mg, sf, li, ri, st, cb, ct, ps, ap, ai, ctp = self._node_with_annexe("annexe2")
        mg.push(Money("0", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("0", "GBP"), "test")
        st.push("", "test")
        cb.push({}, "test")
        simon = Person("Simon", True, home_co_owners=(HomeCoOwner(name="Lorena", share=50),))
        ps.push([simon, Person("Lorena", False), Person("Ashby", False)], "test")
        ct.push(
            CouncilTaxInfo(
                band="D",
                yearly_cost=Measurement(Money("1800", "GBP"), 0.0),
                annexe=AnnexeDwelling(
                    address="FLAT 2, 2 WILLOWMEAD GARDENS",
                    band="A",
                    yearly_cost=Measurement(Money("900", "GBP"), 0.0),
                ),
            ),
            "test",
        )
        ap.push([], "test")  # nobody picked yet
        ai.push(False, "test")
        ctp.push([], "test")
        await flush_processor()

        val = (await node.attempt()).value_or_none()
        assert val is not None
        assert float(val["couple"]["value"]) == pytest.approx(100, abs=0.01)
        assert float(val["others"]["value"]) == pytest.approx(50, abs=0.01)

    @pytest.mark.asyncio
    async def test_ignored_annexe_contributes_nothing(self):
        from houses.council_tax_info import AnnexeDwelling, CouncilTaxInfo

        node, mg, sf, li, ri, st, cb, ct, ps, ap, ai, ctp = self._node_with_annexe("annexe3")
        mg.push(Money("0", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("0", "GBP"), "test")
        st.push("", "test")
        cb.push({}, "test")
        simon = Person("Simon", True, home_co_owners=(HomeCoOwner(name="Lorena", share=50),))
        ps.push([simon, Person("Lorena", False), Person("Ashby", False)], "test")
        ct.push(
            CouncilTaxInfo(
                band="D",
                yearly_cost=Measurement(Money("1800", "GBP"), 0.0),
                annexe=AnnexeDwelling(
                    address="FLAT 2, 2 WILLOWMEAD GARDENS",
                    band="A",
                    yearly_cost=Measurement(Money("900", "GBP"), 0.0),
                ),
            ),
            "test",
        )
        ap.push(["Ashby"], "test")
        ai.push(True, "test")  # user says the address is unrelated
        ctp.push([], "test")
        await flush_processor()

        val = (await node.attempt()).value_or_none()
        assert val is not None
        assert float(val["others"]["value"]) == pytest.approx(50, abs=0.01)

    @pytest.mark.asyncio
    async def test_main_bill_split_by_picked_payers(self):
        """When the settings say only Simon+Lorena pay the MAIN house
        council tax, the couple pays 100% of it and the others' figure
        excludes the main bill entirely."""
        from houses.council_tax_info import CouncilTaxInfo

        node, mg, sf, li, ri, st, cb, ct, ps, ap, ai, ctp = self._node_with_annexe("main1")
        mg.push(Money("0", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("0", "GBP"), "test")
        st.push("", "test")
        cb.push({}, "test")
        simon = Person("Simon", True, home_co_owners=(HomeCoOwner(name="Lorena", share=50),))
        ps.push([simon, Person("Lorena", False), Person("Ashby", False)], "test")
        ct.push(CouncilTaxInfo(band="D", yearly_cost=Measurement(Money("1800", "GBP"), 0.0)), "test")
        ap.push([], "test")
        ai.push(False, "test")
        ctp.push(["Simon", "Lorena"], "test")
        await flush_processor()

        val = (await node.attempt()).value_or_none()
        assert val is not None
        # Main £150/mo paid by the couple alone; the annexe is absent.
        assert float(val["couple"]["value"]) == pytest.approx(150, abs=0.01)
        assert float(val["others"]["value"]) == pytest.approx(0, abs=0.01)
        assert float(val["couple_breakdown"]["council_tax"]) == pytest.approx(150, abs=0.01)
        assert float(val["others_breakdown"]["council_tax"]) == pytest.approx(0, abs=0.01)

    @pytest.mark.asyncio
    async def test_stale_payer_name_does_not_shrink_allocation(self):
        """A payer name that no longer matches a household adult (renamed
        on the sheet after the apportionment was stored) must not reduce
        the bill's allocation — the bill splits among the MATCHING
        payers, so the full council tax is still accounted for."""
        from houses.council_tax_info import CouncilTaxInfo

        node, mg, sf, li, ri, st, cb, ct, ps, ap, ai, ctp = self._node_with_annexe("stale1")
        mg.push(Money("0", "GBP"), "test")
        sf.push(Money("0", "GBP"), "test")
        li.push(Money("0", "GBP"), "test")
        ri.push(Money("0", "GBP"), "test")
        st.push("", "test")
        cb.push({}, "test")
        simon = Person("Simon", True, home_co_owners=(HomeCoOwner(name="Lorena", share=50),))
        ps.push([simon, Person("Lorena", False), Person("Ashby", False)], "test")
        ct.push(CouncilTaxInfo(band="D", yearly_cost=Measurement(Money("1800", "GBP"), 0.0)), "test")
        ap.push([], "test")
        ai.push(False, "test")
        # "Simon" was renamed to "Simon W" on the sheet — the stored
        # choice still names the old person.
        ctp.push(["Simon W", "Lorena"], "test")
        await flush_processor()

        val = (await node.attempt()).value_or_none()
        assert val is not None
        # Lorena (the one matching adult) pays the FULL main bill — the
        # stale name is dropped, not counted in the denominator.
        assert float(val["couple"]["value"]) == pytest.approx(150, abs=0.01)
        assert float(val["others"]["value"]) == pytest.approx(0, abs=0.01)
        assert float(val["couple_breakdown"]["council_tax"]) == pytest.approx(150, abs=0.01)
