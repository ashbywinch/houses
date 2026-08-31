from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import override

from money import Money

from dag.attempt import Attempt, Provenance
from dag.derived_node import DerivedNode
from dag.expression import Attr, Conditional, Div, Field, Literal, Ref
from dag.measurement import Measurement
from dag.node import Node
from houses.model.domain import joint_owner_names


@dataclass(frozen=True)
class GroupFigureResult:
    """(value, stddev, breakdown) bundle from group_figure — named so the
    two call sites read the fields by meaning, not position."""

    value: Decimal
    stddev: float
    breakdown: dict


@dataclass(frozen=True)
class HousingCostConfig:
    """Wiring for the monthly-housing-cost nodes: the component input nodes.

    Shared by ``TotalMonthlyHousingCostNode`` and ``GroupMonthlyCostNode``;
    the group node's apportionment inputs (``persons_source`` and the
    annexe/council-tax payer nodes) are optional and become conditional
    deps only when present.
    """

    monthly_mortgage_node: Node
    yearly_sinking_fund_node: Node
    life_insurance_node: Node
    rental_income_node: Node
    status_node: Node
    commute_breakdown_node: Node
    council_tax_node: Node
    persons_source: Node | None = None
    annexe_payers_node: Node | None = None
    annexe_ignored_node: Node | None = None
    council_tax_payers_node: Node | None = None


@dataclass(frozen=True)
class GroupCostInputs:
    """Compute inputs for ``GroupMonthlyCostNode.compute``, bound by dep name."""

    mortgage: Attempt[Money]
    sinking: Attempt[Money]
    life_insurance: Attempt[Money]
    rental_income: Attempt[Money]
    status: Attempt[str]
    commute: Attempt[dict]
    council_tax: Attempt
    persons: Attempt[list]
    annexe_payers: Attempt[list] | None = None
    annexe_ignored: Attempt[bool] | None = None
    council_tax_payers: Attempt[list] | None = None


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
        config: HousingCostConfig,
    ):
        self._status_node: Node = config.status_node
        self._sinking_node: Node = config.yearly_sinking_fund_node
        self._life_insurance_node: Node = config.life_insurance_node
        super().__init__(
            node_id,
            Measurement[Money],
            (
                config.monthly_mortgage_node,
                config.rental_income_node,
                config.status_node,
                config.commute_breakdown_node,
                config.council_tax_node,
                config.yearly_sinking_fund_node,
                config.life_insurance_node,
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
                if_false=Literal(Money(amount="0", currency="GBP")),
            )
            + Conditional(
                predicate=lambda: bool(
                    (v3 := self._deps[3].latest_attempt().value_or_none())
                    and isinstance(v3, dict)
                    and "yearly_total_gbp" in v3
                ),
                if_true=Div(Field(Ref(self._deps[3]), "yearly_total_gbp"), Literal(12)),
                if_false=Literal(Money(amount="0", currency="GBP")),
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
                if_false=Literal(Money(amount="0", currency="GBP")),
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
# lucidlint: ignore middle-man protocol/reflected-operator requirement
    def compute(self, **kwargs) -> Attempt[Measurement[Money]]:
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
        config: HousingCostConfig,
    ):
        self._annexe_payers_node: Node | None = config.annexe_payers_node
        self._annexe_ignored_node: Node | None = config.annexe_ignored_node
        self._council_tax_payers_node: Node | None = config.council_tax_payers_node
        persons_source = config.persons_source
        if persons_source is None:
            # compute() binds the "persons" attempt by name — without the
            # source every evaluation would fail, so refuse at construction.
            raise ValueError(f"{node_id}: group monthly cost requires persons_source")
        deps = (
            config.monthly_mortgage_node,
            config.yearly_sinking_fund_node,
            config.life_insurance_node,
            config.rental_income_node,
            config.status_node,
            config.commute_breakdown_node,
            config.council_tax_node,
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
        dep_list = list(deps)
        for optional_node, name in (
            (config.annexe_payers_node, "annexe_payers"),
            (config.annexe_ignored_node, "annexe_ignored"),
            (config.council_tax_payers_node, "council_tax_payers"),
        ):
            if optional_node is not None:
                dep_list.append(optional_node)
                names.append(name)
        deps = tuple(dep_list)
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
    @staticmethod
    def compute(**kwargs) -> Attempt[dict]:
        return _compute_group_costs(GroupCostInputs(**kwargs))

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


MONTHS_PER_YEAR = 12
SHARE_DECIMALS = 4


class _GroupCostCalculator:
    """Per-group monthly figures for the GroupMonthlyCostNode.

    The couple/others apportionment (commute spread, council-tax payer
    split, sinking-fund and insurance scaling) operates on the bound
    compute inputs — the free functions that shared the leading
    ``GroupCostInputs`` parameter are this class's methods.
    """

    def __init__(self, inputs: GroupCostInputs):
        self.inputs: GroupCostInputs = inputs

    def adults_and_owners(self) -> _AdultsSplit:
        """The adult/owner/other split of the persons attempt."""
        ps = self.inputs.persons.value_or_none() or []
        adults = [p for p in ps if not getattr(p, "is_child", False)]
        owners = joint_owner_names(ps)
        others = [p for p in adults if p.name not in owners]
        return _AdultsSplit(adults, owners, others)

    def is_current(self) -> bool:
        return self.inputs.status.succeeded and (self.inputs.status.value_or_none() or "").strip().lower() == "current"

    def council_monthly_and_stddev(self) -> _CouncilMonthly:
        """The main house council tax as a monthly amount + yearly stddev."""
        council = self.inputs.council_tax.value_or_none()
        yearly = council.yearly_cost if council is not None else None
        if yearly is None:
            return _CouncilMonthly(Decimal(0), 0.0)
        monthly = Decimal(str(yearly.value.amount)) / Decimal(MONTHS_PER_YEAR)
        return _CouncilMonthly(monthly, float(yearly.stddev))

    @staticmethod
    def money_of(person, field: str) -> Decimal:
        """A person's monthly money field as a Decimal (0 when absent)."""
        val = getattr(person, field, None)
        return val.amount if val is not None else Decimal(0)

    def commute_monthly(self, name: str) -> Decimal:
        """The person's yearly commute cost spread to months."""
        per = (self.inputs.commute.value_or_none() or {}).get("persons") or {}
        yearly = (per.get(name) or {}).get("yearly_gbp") or 0
        return Decimal(str(yearly)) / Decimal(MONTHS_PER_YEAR)

    def payer_split(self, adults: list, council, ignored: bool) -> _PayerSplit:
        """Resolve the council-tax payer sets and the annexe allocation.

        Names are matched against the household: a stale payer (renamed
        or removed on the sheet after the apportionment was stored) is
        dropped, so the bill splits among the MATCHING payers and the
        full amount is still allocated (a stale name must not shrink the
        share by counting in the denominator).
        """
        adult_names = {p.name for p in adults}
        main_payers: set[str] = set()
        if self.inputs.council_tax_payers is not None and self.inputs.council_tax_payers.succeeded:
            main_payers = set(self.inputs.council_tax_payers.value_or_none() or []) & adult_names
        main_payer_total = len(main_payers) if main_payers else len(adults)
        alloc = self._annexe_allocation(council, adult_names, ignored)
        return _PayerSplit(
            frozenset(main_payers), main_payer_total, alloc.monthly, alloc.stddev, alloc.payers
        )

    def _annexe_allocation(self, council, adult_names: set[str], ignored: bool) -> _AnnexeAllocation:
        """The annexe bill (if any): who pays it, monthly amount, stddev."""
        annexe = council.annexe if council is not None else None
        if annexe is None or annexe.yearly_cost is None or ignored:
            return _AnnexeAllocation(Decimal(0), 0.0, frozenset())
        payers: set[str] = set()
        if self.inputs.annexe_payers is not None and self.inputs.annexe_payers.succeeded:
            stored_payers = set(self.inputs.annexe_payers.value_or_none() or [])
            if stored_payers:
                payers = stored_payers & adult_names
                if not payers:
                    # All stored names are stale — the annexe bill must
                    # not silently vanish; mirror the main-bill path.
                    payers = adult_names
        if not payers:
            return _AnnexeAllocation(Decimal(0), 0.0, frozenset())
        monthly = Decimal(str(annexe.yearly_cost.value.amount)) / Decimal(MONTHS_PER_YEAR)
        stddev = float(annexe.yearly_cost.stddev) if annexe.yearly_cost.stddev else 0.0
        return _AnnexeAllocation(monthly, stddev, frozenset(payers))

    def group_figure(
        self,
        ctx: _GroupCostContext,
        group: list,
        owner_share: float,
        rent: Decimal,
    ) -> GroupFigureResult:
        """One group's (couple/others) monthly figure + breakdown."""
        split = ctx.split
        commutes = sum((self.commute_monthly(p.name) for p in group), Decimal(0))
        insurance = ctx.insurance_scale * sum((self.money_of(p, "life_insurance_monthly") for p in group), Decimal(0))
        share = Decimal(str(round(owner_share, SHARE_DECIMALS)))
        main_payer_count = sum(1 for p in group if p.name in split.main_payers) if split.main_payers else len(group)
        main_share = (
            Decimal(str(round(main_payer_count / split.main_payer_total, SHARE_DECIMALS))) * ctx.council_monthly
            if split.main_payers
            else share * ctx.council_monthly
        )
        payer_count = sum(1 for p in group if p.name in split.payers)
        annexe_share = (
            Decimal(str(round(payer_count / len(split.payers), SHARE_DECIMALS))) * split.annexe_monthly
            if split.payers
            else Decimal(0)
        )
        council_share = main_share + annexe_share
        sinking_share = share * ctx.sinking_monthly
        value = commutes + insurance + council_share + sinking_share + rent
        stddev = owner_share * float(ctx.council_stddev) / float(MONTHS_PER_YEAR)
        if split.main_payers:
            stddev = (main_payer_count / split.main_payer_total) * float(ctx.council_stddev) / float(MONTHS_PER_YEAR)
        if split.payers:
            stddev += (payer_count / len(split.payers)) * split.annexe_stddev / float(MONTHS_PER_YEAR)
        # lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        breakdown = {
            "commutes": round(float(commutes), 2),
            "insurance": round(float(insurance), 2),
            # Main bill only — the annexe bill has its own row below, so
            # the breakdown rows sum to the group total.
            "council_tax": round(float(main_share), 2),
            "sinking_fund": round(float(sinking_share), 2),
        }
        if annexe_share:
            breakdown["annexe_council_tax"] = round(float(annexe_share), 2)
        return GroupFigureResult(value, round(stddev, 2), breakdown)


@dataclass(frozen=True)
class _AdultsSplit:
    """The adult/owner/other split of the household."""

    adults: list
    owners: set[str]
    others: list


@dataclass(frozen=True)
class _CouncilMonthly:
    """The main house council tax: monthly amount + yearly stddev."""

    monthly: Decimal
    stddev: float


@dataclass(frozen=True)
class _AnnexeAllocation:
    """The annexe bill allocation: monthly amount, stddev, payers."""

    monthly: Decimal
    stddev: float
    payers: frozenset[str]


@dataclass(frozen=True)
class _PayerSplit:
    """Who pays the main bill and the annexe bill (if any)."""

    main_payers: frozenset[str]
    main_payer_total: int
    annexe_monthly: Decimal
    annexe_stddev: float
    payers: frozenset[str]


@dataclass(frozen=True)
class _GroupCostContext:
    """Shared computed values for the per-group figures."""

    split: _PayerSplit
    council_monthly: Decimal
    council_stddev: float
    sinking_monthly: Decimal
    insurance_scale: int


def _group_figure_result(calc, ctx, group, owner_share, rent) -> GroupFigureResult:
    """One group's monthly figure, stddev and breakdown (rent applied)."""
    fig = calc.group_figure(ctx, group, owner_share, rent)
    if rent:
        fig.breakdown["rent_paid"] = round(float(rent), 2)
    return fig


def _compute_group_costs(inputs: GroupCostInputs) -> Attempt[dict]:
    """Compute the couple/others monthly figures from the bound inputs."""
    calc = _GroupCostCalculator(inputs)
    split = calc.adults_and_owners()
    adults, owners, others = split.adults, split.owners, split.others
    if not adults:
        return Attempt.succeeded({"couple": None, "others": None, "couple_label": "", "others_label": ""})
    is_current = calc.is_current()
    council = calc.council_monthly_and_stddev()
    sinking_monthly = (
        Decimal(0)
        if is_current
        else (inputs.sinking.value_or_none() or Money(amount="0", currency="GBP")).amount / Decimal(MONTHS_PER_YEAR)
    )
    insurance_scale = 0 if is_current else 1
    ignored = (
        bool(inputs.annexe_ignored.value_or_none())
        if inputs.annexe_ignored is not None and inputs.annexe_ignored.succeeded
        else False
    )
    ctx = _GroupCostContext(
        split=calc.payer_split(adults, inputs.council_tax.value_or_none(), ignored),
        council_monthly=council.monthly,
        council_stddev=council.stddev,
        sinking_monthly=sinking_monthly,
        insurance_scale=insurance_scale,
    )

    return _assemble_result(calc, ctx, adults, owners, others)


def _assemble_result(calc, ctx, adults, owners, others) -> Attempt[dict]:
    """Build the couple/others figures and the wire-format result dict."""
    is_current = calc.is_current()
    rent_scale = 1 if is_current else 0
    couple_rent_paid = rent_scale * sum(
        (calc.money_of(p, "rent_paid_monthly") for p in adults if p.name in owners), Decimal(0)
    )
    others_rent_paid = rent_scale * sum((calc.money_of(p, "rent_paid_monthly") for p in others), Decimal(0))
    mortgage_val = (calc.inputs.mortgage.value_or_none() or Money(amount="0", currency="GBP")).amount
    rental_val = (calc.inputs.rental_income.value_or_none() or Money(amount="0", currency="GBP")).amount

    owner_share = len(owners) / len(adults)
    others_share = len(others) / len(adults)

    couple_fig = _group_figure_result(
        calc, ctx, [p for p in adults if p.name in owners], owner_share, couple_rent_paid
    )
    couple_val = couple_fig.value + mortgage_val - rental_val
    couple_std, couple_breakdown = couple_fig.stddev, couple_fig.breakdown
    couple_breakdown["mortgage"] = round(float(mortgage_val), 2)
    couple_breakdown["rental_income"] = round(-float(rental_val), 2)
    others_fig = _group_figure_result(calc, ctx, others, others_share, others_rent_paid)
    others_val, others_std, others_breakdown = others_fig.value, others_fig.stddev, others_fig.breakdown
    return Attempt.succeeded(
        # lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        {
            # lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape
            "couple": {"value": f"{couple_val:.2f}", "stddev": couple_std},
            # lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape
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


