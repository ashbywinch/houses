from __future__ import annotations

from decimal import Decimal
from typing import override

from money import Money

from dag.attempt import Attempt, Provenance
from dag.derived_node import DerivedNode
from dag.expression import Attr, Conditional, Div, Field, Literal, Ref
from dag.measurement import Measurement


class TotalMonthlyHousingCostNode(DerivedNode[Measurement[Money]]):
    """Total monthly housing cost.

    = Mortgage + SinkingFund(monthly) + LifeInsurance + Commute + CouncilTax - RentalIncome
    When Status is "Current", sinking fund and life insurance are excluded.

    The value is a ``Measurement``: exact (stddev 0) when every component
    is exact; approximate when a component (council tax estimate) carries
    a spread — the total "inherits ≈" (Part A).
    """

    def __init__(
        self,
        node_id: str,
        *,
        monthly_mortgage_node,
        yearly_sinking_fund_node,
        life_insurance_node,
        rental_income_node,
        status_node,
        commute_breakdown_node,
        council_tax_node,
    ):
        self._status_node = status_node
        self._sinking_node = yearly_sinking_fund_node
        self._life_insurance_node = life_insurance_node
        super().__init__(
            node_id,
            Measurement[Money],
            (
                monthly_mortgage_node,
                rental_income_node,
                status_node,
                commute_breakdown_node,
                council_tax_node,
                yearly_sinking_fund_node,
                life_insurance_node,
            ),
        )

    @property
    def expression(self):
        return (
            Ref(self._deps[0])  # mortgage
            + Conditional(
                predicate=lambda: (
                    (self._status_node.latest_attempt().value_or_none() or "").strip().lower() != "current"
                ),
                if_true=(
                    Div(Ref(self._sinking_node), Literal(12))
                    + Ref(self._life_insurance_node)
                ),
                if_false=Literal(Money("0", "GBP")),
            )
            + Conditional(
                predicate=lambda: bool(
                    self._deps[3].latest_attempt().value_or_none()
                    and isinstance(self._deps[3].latest_attempt().value_or_none(), dict)
                    and "yearly_total_gbp" in self._deps[3].latest_attempt().value_or_none()
                ),
                if_true=Div(Field(Ref(self._deps[3]), "yearly_total_gbp"), Literal(12)),
                if_false=Literal(Money("0", "GBP")),
            )
            + Conditional(
                predicate=lambda: bool(
                    self._deps[4].latest_attempt().succeeded
                    and self._deps[4].latest_attempt().value_or_none() is not None
                    and hasattr(self._deps[4].latest_attempt().value_or_none(), "yearly_cost")
                    and self._deps[4].latest_attempt().value_or_none().yearly_cost is not None
                ),
                if_true=Div(Attr(Ref(self._deps[4]), "yearly_cost"), Literal(12)),
                if_false=Literal(Money("0", "GBP")),
            )
            - Ref(self._deps[1])  # rental_income
        )

    def _get_active_deps(self):
        status_att = self._status_node.latest_attempt()
        is_current = status_att.succeeded and (status_att.value_or_none() or "").strip().lower() == "current"
        if is_current:
            return self._deps[:5]
        return self._deps

    def compute(
        self,
        mortgage: Attempt[Money],
        rental_income: Attempt[Money],
        status: Attempt[str],
        commute: Attempt[dict],
        council_tax: Attempt,
        sinking: Attempt[Money] | None = None,
        life_insurance: Attempt[Money] | None = None,
    ) -> Attempt[Measurement[Money]]:
        return self.expression.evaluate()


class GroupMonthlyCostNode(DerivedNode[dict]):
    """The headline's TWO numbers: the joint owners' monthly cost and
    the other adults'.

    The joint owners (current-home holder + co-owners — the couple who
    will carry the mortgage) are the deal-breaker; the others' costs
    are shown too so nobody's figure is hidden. Shared costs (council
    tax, sinking fund of the new house) split evenly across ALL adults,
    each group's shares counted. The couple's figure subtracts rent
    received from the others; the others' figure adds the rent they pay.
    """

    def __init__(
        self,
        node_id: str,
        *,
        monthly_mortgage_node,
        yearly_sinking_fund_node,
        life_insurance_node,
        rental_income_node,
        status_node,
        commute_breakdown_node,
        council_tax_node,
        persons_source,
    ):
        super().__init__(
            node_id,
            dict,
            (
                monthly_mortgage_node,
                yearly_sinking_fund_node,
                life_insurance_node,
                rental_income_node,
                status_node,
                commute_breakdown_node,
                council_tax_node,
                persons_source,
            ),
        )

    def compute(
        self,
        mortgage: Attempt[Money],
        sinking: Attempt[Money],
        life_insurance: Attempt[Money],
        rental_income: Attempt[Money],
        status: Attempt[str],
        commute: Attempt[dict],
        council_tax: Attempt,
        persons: Attempt[list],
    ) -> Attempt[dict]:
        from houses.model.domain import joint_owner_names

        ps = persons.value_or_none() or []
        adults = [p for p in ps if not getattr(p, "is_child", False)]
        if not adults:
            return Attempt.succeeded({"couple": None, "others": None, "couple_label": "", "others_label": ""})
        owners = joint_owner_names(ps)
        others = [p for p in adults if p.name not in owners]
        n_adults = len(adults)

        def money_of(p, field: str) -> Decimal:
            val = getattr(p, field, None)
            return val.amount if val is not None else Decimal(0)

        def commute_monthly(name: str) -> Decimal:
            per = (commute.value_or_none() or {}).get("persons") or {}
            yearly = (per.get(name) or {}).get("yearly_gbp") or 0
            return Decimal(str(yearly)) / Decimal(12)

        is_current = status.succeeded and (status.value_or_none() or "").strip().lower() == "current"
        council = council_tax.value_or_none()
        # CouncilTaxInfo carries the cost as a Measurement at
        # yearly_cost (exact when looked up, a spread when the Band-D
        # fallback estimated it) — read the real fields, never a
        # getattr-with-default that silently zeroes the contribution.
        council_yearly = council.yearly_cost if council is not None else None
        council_stddev = float(council_yearly.stddev) if council_yearly is not None else 0.0
        council_monthly = (
            Decimal(str(council_yearly.value.amount)) / Decimal(12)
            if council_yearly is not None
            else Decimal(0)
        )
        # For a Current property the sinking fund and life insurance are
        # excluded (the family's current living cost, not the purchase).
        sinking_monthly = Decimal(0) if is_current else (sinking.value_or_none() or Money("0", "GBP")).amount / Decimal(12)  # noqa: E501
        insurance_scale = 0 if is_current else 1

        def group_figure(group: list, owner_share: float, rent: Decimal) -> tuple[Decimal, float, dict]:
            commutes = sum((commute_monthly(p.name) for p in group), Decimal(0))
            insurance = insurance_scale * sum((money_of(p, "life_insurance_monthly") for p in group), Decimal(0))
            share = Decimal(str(round(owner_share, 4)))
            council_share = share * council_monthly
            sinking_share = share * sinking_monthly
            value = commutes + insurance + council_share + sinking_share + rent
            stddev = owner_share * float(council_stddev) / 12.0
            breakdown = {
                "commutes": round(float(commutes), 2),
                "insurance": round(float(insurance), 2),
                "council_tax": round(float(council_share), 2),
                "sinking_fund": round(float(sinking_share), 2),
            }
            return value, round(stddev, 2), breakdown

        others_rent = sum((money_of(p, "rent_paid_monthly") for p in others), Decimal(0))
        # The rent the others pay to the couple is the CURRENT-home
        # arrangement — it only applies when THIS property is the current
        # home.  A new purchase has no rent transfer in either direction.
        rent_scale = 1 if is_current else 0
        others_rent = rent_scale * others_rent
        couple_rent = others_rent  # the rent the others pay goes to the couple
        mortgage_val = (mortgage.value_or_none() or Money("0", "GBP")).amount
        rental_val = (rental_income.value_or_none() or Money("0", "GBP")).amount

        owner_share = len(owners) / n_adults
        others_share = len(others) / n_adults

        couple_val, couple_std, couple_breakdown = group_figure(
            [p for p in adults if p.name in owners], owner_share, -couple_rent
        )
        couple_val += mortgage_val - rental_val
        couple_breakdown["mortgage"] = round(float(mortgage_val), 2)
        couple_breakdown["rental_income"] = round(-float(rental_val), 2)
        if is_current:
            couple_breakdown["rent_received"] = round(-float(couple_rent), 2)
        others_val, others_std, others_breakdown = group_figure(others, others_share, others_rent)
        if is_current:
            others_breakdown["rent_paid"] = round(float(others_rent), 2)

        return Attempt.succeeded(
            {
                "couple": {"value": f"{couple_val:.2f}", "stddev": couple_std},
                "others": {"value": f"{others_val:.2f}", "stddev": others_std},
                "couple_label": "+".join(p.name[0].upper() for p in adults if p.name in owners),
                # Full names, not initials — the detail page says "Ashby",
                # not "A". The card still uses the short couple label.
                "couple_names": "+".join(p.name for p in adults if p.name in owners),
                "others_label": "+".join(p.name for p in others),
                "couple_breakdown": couple_breakdown,
                "others_breakdown": others_breakdown,
            }
        )

    @override
    async def build_provenance(self) -> Provenance:
        """The monthly figures as a human summary, never the raw dict.

        The node VALUE is the breakdown dict (the UI renders the rows
        from it), but the provenance trust bar leads with the value —
        dumping the dict read "couple: value: 3753.21, stddev: …".
        """
        prov = await super().build_provenance()
        val = self._attempt.value_or_none()
        if isinstance(val, dict):
            couple = val.get("couple") or {}
            others = val.get("others") or {}
            parts = []
            if couple.get("value") is not None:
                owner = val.get("couple_names") or val.get("couple_label") or ""
                parts.append(f"{owner} £{Decimal(couple['value']):,.2f}/mo".strip())
            if others.get("value") is not None:
                label = val.get("others_label") or ""
                parts.append(f"{label} £{Decimal(others['value']):,.2f}/mo".strip())
            if parts:
                prov.value = ", ".join(parts)
        return prov
