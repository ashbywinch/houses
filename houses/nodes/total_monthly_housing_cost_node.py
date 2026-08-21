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
            dep_names=(
                "mortgage",
                "rental_income",
                "status",
                "commute",
                "council_tax",
                "sinking",
                "life_insurance",
            ),
        )

    @property
    @override
    def expression(self):
        return (
            Ref(self._deps[0])  # mortgage
            + Conditional(
                predicate=lambda: (
                    (self._status_node.latest_attempt().value_or_none() or "").strip().lower() != "current"
                ),
                if_true=(Div(Ref(self._sinking_node), Literal(12)) + Ref(self._life_insurance_node)),
                if_false=Literal(Money("0", "GBP")),
            )
            + Conditional(
                predicate=lambda: bool(
                    (v3 := self._deps[3].latest_attempt().value_or_none())
                    and isinstance(v3, dict)
                    and "yearly_total_gbp" in v3
                ),
                if_true=Div(Field(Ref(self._deps[3]), "yearly_total_gbp"), Literal(12)),
                if_false=Literal(Money("0", "GBP")),
            )
            + Conditional(
                predicate=lambda: bool(
                    self._deps[4].latest_attempt().succeeded
                    and self._deps[4].latest_attempt().value_or_none() is not None
                    and (v4 := self._deps[4].latest_attempt().value_or_none()) is not None
                    and hasattr(v4, "yearly_cost")
                    and v4.yearly_cost is not None
                ),
                if_true=Div(Attr(Ref(self._deps[4]), "yearly_cost"), Literal(12)),
                if_false=Literal(Money("0", "GBP")),
            )
            - Ref(self._deps[1])  # rental_income
        )

    @override
    def _get_active_deps(self):
        status_att = self._status_node.latest_attempt()
        is_current = status_att.succeeded and (status_att.value_or_none() or "").strip().lower() == "current"
        if is_current:
            return self._deps[:5]
        return self._deps

    @override
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
        annexe_payers_node=None,
        annexe_ignored_node=None,
        council_tax_payers_node=None,
    ):
        self._annexe_payers_node = annexe_payers_node
        self._annexe_ignored_node = annexe_ignored_node
        self._council_tax_payers_node = council_tax_payers_node
        deps = (
            monthly_mortgage_node,
            yearly_sinking_fund_node,
            life_insurance_node,
            rental_income_node,
            status_node,
            commute_breakdown_node,
            council_tax_node,
            persons_source,
        )
        names = [
            "mortgage",
            "sinking",
            "life_insurance",
            "rental_income",
            "status",
            "commute",
            "council_tax",
            "persons",
        ]
        if annexe_payers_node is not None:
            deps = deps + (annexe_payers_node,)
            names.append("annexe_payers")
        if annexe_ignored_node is not None:
            deps = deps + (annexe_ignored_node,)
            names.append("annexe_ignored")
        if council_tax_payers_node is not None:
            deps = deps + (council_tax_payers_node,)
            names.append("council_tax_payers")
        super().__init__(
            node_id,
            dict,
            deps,
            # Bind attempts by name: the three apportionment deps are
            # appended conditionally and were the historical source of
            # positional misalignment (a dropped/pending dep shifted
            # council_tax_payers into annexe_payers).
            dep_names=tuple(names),
        )

    @override
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
        annexe_payers: Attempt[list] | None = None,
        annexe_ignored: Attempt[bool] | None = None,
        council_tax_payers: Attempt[list] | None = None,
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
            Decimal(str(council_yearly.value.amount)) / Decimal(12) if council_yearly is not None else Decimal(0)
        )
        # For a Current property the sinking fund and life insurance are
        # excluded (the family's current living cost, not the purchase).
        sinking_monthly = (
            Decimal(0) if is_current else (sinking.value_or_none() or Money("0", "GBP")).amount / Decimal(12)
        )  # noqa: E501
        insurance_scale = 0 if is_current else 1
        # Main house council tax: by default it splits across ALL adults
        # (each group's headcount share — the historical behaviour).  The
        # user can instead pick WHO pays it (e.g. only the owners) — the
        # picked people split the bill equally among themselves.
        # Names are matched against the household: a stale payer (renamed/
        # removed on the sheet after the apportionment was stored) is
        # dropped, so the bill splits among the MATCHING payers and the
        # full amount is still allocated (a stale name must not shrink
        # the share by counting in the denominator).
        adult_names = {p.name for p in adults}
        main_payers: set[str] = set()
        if council_tax_payers is not None and council_tax_payers.succeeded:
            main_payers = set(council_tax_payers.value_or_none() or []) & adult_names
        main_payer_total = len(main_payers) if main_payers else n_adults

        # Annexe: a second dwelling at the same address has its OWN council
        # tax bill.  Only the people the user picked pay it, split equally
        # among themselves; until anyone is picked (or the user says the
        # address is unrelated) it contributes nothing.
        annexe = council.annexe if council is not None else None
        ignored = (
            bool(annexe_ignored.value_or_none()) if annexe_ignored is not None and annexe_ignored.succeeded else False
        )
        payers: set[str] = set()
        annexe_monthly = Decimal(0)
        annexe_stddev = 0.0
        if annexe is not None and annexe.yearly_cost is not None and not ignored:
            if annexe_payers is not None and annexe_payers.succeeded:
                payers = set(annexe_payers.value_or_none() or []) & adult_names
            if payers:
                annexe_monthly = Decimal(str(annexe.yearly_cost.value.amount)) / Decimal(12)
                annexe_stddev = float(annexe.yearly_cost.stddev) if annexe.yearly_cost.stddev else 0.0

        def group_figure(group: list, owner_share: float, rent: Decimal) -> tuple[Decimal, float, dict]:
            commutes = sum((commute_monthly(p.name) for p in group), Decimal(0))
            insurance = insurance_scale * sum((money_of(p, "life_insurance_monthly") for p in group), Decimal(0))
            share = Decimal(str(round(owner_share, 4)))
            main_payer_count = sum(1 for p in group if p.name in main_payers) if main_payers else len(group)
            main_share = (
                Decimal(str(round(main_payer_count / main_payer_total, 4))) * council_monthly
                if main_payers
                else share * council_monthly
            )
            payer_count = sum(1 for p in group if p.name in payers)
            annexe_share = Decimal(str(round(payer_count / len(payers), 4))) * annexe_monthly if payers else Decimal(0)
            council_share = main_share + annexe_share
            sinking_share = share * sinking_monthly
            value = commutes + insurance + council_share + sinking_share + rent
            stddev = owner_share * float(council_stddev) / 12.0
            if main_payers:
                stddev = (main_payer_count / main_payer_total) * float(council_stddev) / 12.0
            if payers:
                stddev += (payer_count / len(payers)) * annexe_stddev / 12.0
            breakdown = {
                "commutes": round(float(commutes), 2),
                "insurance": round(float(insurance), 2),
                "council_tax": round(float(council_share), 2),
                "sinking_fund": round(float(sinking_share), 2),
            }
            if annexe_share:
                breakdown["annexe_council_tax"] = round(float(annexe_share), 2)
            return value, round(stddev, 2), breakdown

        # Rent is per-person, never a transfer between groups: an adult's
        # rent_paid is THEIR cost (we don't know who they pay), and the
        # property's rental income is its owners' income (we don't know who
        # pays it).  The couple's figure must never absorb the others'
        # rent — that double-counted the same money even when the payer
        # really paid the couple.
        rent_scale = 1 if is_current else 0
        couple_rent_paid = rent_scale * sum(
            (money_of(p, "rent_paid_monthly") for p in adults if p.name in owners), Decimal(0)
        )
        others_rent_paid = rent_scale * sum((money_of(p, "rent_paid_monthly") for p in others), Decimal(0))
        mortgage_val = (mortgage.value_or_none() or Money("0", "GBP")).amount
        rental_val = (rental_income.value_or_none() or Money("0", "GBP")).amount

        owner_share = len(owners) / n_adults
        others_share = len(others) / n_adults

        couple_val, couple_std, couple_breakdown = group_figure(
            [p for p in adults if p.name in owners], owner_share, couple_rent_paid
        )
        couple_val += mortgage_val - rental_val
        couple_breakdown["mortgage"] = round(float(mortgage_val), 2)
        couple_breakdown["rental_income"] = round(-float(rental_val), 2)
        if couple_rent_paid:
            couple_breakdown["rent_paid"] = round(float(couple_rent_paid), 2)
        others_val, others_std, others_breakdown = group_figure(others, others_share, others_rent_paid)
        if others_rent_paid:
            others_breakdown["rent_paid"] = round(float(others_rent_paid), 2)

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
            # Explain an annexe allocation: whose share of the second
            # dwelling's council tax is inside these figures.
            allocated = (val.get("couple_breakdown") or {}).get("annexe_council_tax") or (
                val.get("others_breakdown") or {}
            ).get("annexe_council_tax")
            if allocated and self._annexe_payers_node is not None:
                payer_att = self._annexe_payers_node.latest_attempt()
                payers = payer_att.value_or_none() if payer_att is not None else None
                if payers:
                    annexe_note = "includes annexe council tax (second dwelling) split between: " + ", ".join(payers)
                    if prov.description is None:
                        prov.description = annexe_note
                    else:
                        prov.description = f"{prov.description} — {annexe_note}"
        return prov
