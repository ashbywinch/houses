"""Test that updating works_estimates propagates through the DAG
to mortgage_required, monthly_mortgage, and total_monthly_cost."""

from __future__ import annotations

import pytest
from money import Money

from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.model.domain import Person


class TestWorksPropagatesToMortgage:
    """When works_estimates changes, mortgage figures must update."""

    @pytest.mark.asyncio
    async def test_works_change_updates_mortgage(self):
        """Pushing a works_estimate must cause mortgage_required to
        reflect the new total in the next detail read."""
        from houses.nodes.equity_total_node import EquityTotalNode
        from houses.nodes.mortgage_required_node import (
            MortgageRequiredNode,
        )
        from houses.nodes.total_works_node import TotalWorksNode

        # ── Source nodes ───────────────────────────────────────────
        price = UserInputNode[Money]("wpm_price", Money)
        sd = UserInputNode[Money]("wpm_sd", Money)
        persons = UserInputNode[list]("wpm_persons", list)
        works = UserInputNode[dict]("wpm_works", dict)
        fin = UserInputNode[dict]("wpm_fin", dict)

        # ── Derived nodes ─────────────────────────────────────────
        te = EquityTotalNode(
            "wpm_te", persons_source=persons,
        )
        tw = TotalWorksNode(
            "wpm_tw",
            persons_source=persons,
            works_estimates_node=works,
        )
        mr = MortgageRequiredNode(
            "wpm_mr",
            rightmove_price=price,
            stamp_duty=sd,
            total_works_node=tw,
            total_equity_node=te,
        )
        # ── Seed data ──────────────────────────────────────────────
        price.push(Money("500000", "GBP"), "test")
        sd.push(Money("15000", "GBP"), "test")
        persons.push(
            [
                Person(
                    name="Simon", has_car=True,
                    home_sale_price=Money("550000", "GBP"),
                    outstanding_mortgage=Money("373000", "GBP"),
                ),
                Person(
                    name="Ashby", has_car=True,
                    cash_contribution=Money("300000", "GBP"),
                ),
            ],
            "test",
        )
        works.push({}, "test")
        fin.push(
            {
                "mortgage_rate": 0.0495,
                "mortgage_term_years": 27,
            },
            "test",
        )

        await flush_processor()
        await flush_processor()

        # ── Baseline: no works yet ────────────────────────────────
        a1 = await mr.attempt()
        assert a1.succeeded
        # Equity: max(0, 550k-373k) + 300k = 177k + 300k = 477k
        # Mortgage = 500k + 15k + 0 - 477k = 38k
        baseline_mortgage = a1.value_or_none()
        assert baseline_mortgage == Money("38000", "GBP"), (
            f"Expected 38000, got {baseline_mortgage}"
        )

        # ── Update works estimate ──────────────────────────────────
        works.push({"Simon": 0, "Ashby": 20000}, "test")

        await flush_processor()
        await flush_processor()

        # ── Verify mortgage changed ───────────────────────────────
        a2 = await mr.attempt()
        assert a2.succeeded
        updated_mortgage = a2.value_or_none()
        # Mortgage = 500k + 15k + 20k - 477k = 58k
        assert updated_mortgage == Money("58000", "GBP"), (
            f"Expected 58000, got {updated_mortgage}"
        )
        # Must be different from baseline
        assert updated_mortgage != baseline_mortgage, (
            "Mortgage did not change after works update"
        )

    @pytest.mark.asyncio
    async def test_monthly_mortgage_updates_with_works(self):
        """Monthly mortgage payment must change when works
        estimate is updated."""
        from houses.nodes.equity_total_node import EquityTotalNode
        from houses.nodes.monthly_mortgage_payment_node import (
            MonthlyMortgagePaymentNode,
        )
        from houses.nodes.mortgage_required_node import (
            MortgageRequiredNode,
        )
        from houses.nodes.total_works_node import TotalWorksNode

        price = UserInputNode[Money]("wpm2_price", Money)
        sd = UserInputNode[Money]("wpm2_sd", Money)
        persons = UserInputNode[list]("wpm2_persons", list)
        works = UserInputNode[dict]("wpm2_works", dict)
        fin = UserInputNode[dict]("wpm2_fin", dict)

        te = EquityTotalNode(
            "wpm2_te", persons_source=persons,
        )
        tw = TotalWorksNode(
            "wpm2_tw",
            persons_source=persons,
            works_estimates_node=works,
        )
        mr = MortgageRequiredNode(
            "wpm2_mr",
            rightmove_price=price,
            stamp_duty=sd,
            total_works_node=tw,
            total_equity_node=te,
        )
        mm = MonthlyMortgagePaymentNode(
            "wpm2_mm",
            mortgage_required_node=mr,
            financial_source=fin,
        )

        price.push(Money("500000", "GBP"), "test")
        sd.push(Money("15000", "GBP"), "test")
        persons.push(
            [
                Person(
                    name="Simon", has_car=True,
                    home_sale_price=Money("550000", "GBP"),
                    outstanding_mortgage=Money("373000", "GBP"),
                ),
                Person(
                    name="Ashby", has_car=True,
                    cash_contribution=Money("300000", "GBP"),
                ),
            ],
            "test",
        )
        works.push({}, "test")
        fin.push(
            {
                "mortgage_rate": 0.0495,
                "mortgage_term_years": 27,
            },
            "test",
        )

        await flush_processor()
        await flush_processor()

        # Baseline monthly payment (mortgage = 38k)
        m1 = await mm.attempt()
        assert m1.succeeded
        baseline_payment = m1.value_or_none().amount

        # Update works
        works.push({"Simon": 0, "Ashby": 20000}, "test")

        await flush_processor()
        await flush_processor()

        # Verify monthly payment changed (mortgage now = 58k)
        m2 = await mm.attempt()
        assert m2.succeeded
        updated_payment = m2.value_or_none().amount
        assert updated_payment != baseline_payment, (
            "Monthly mortgage did not change after works update"
        )
