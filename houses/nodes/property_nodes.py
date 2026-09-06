from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from money import Money

import dag.derived_node as _dag_derived
import dag.persistence as _dag_per
from dag.derived_node import DerivedNode
from dag.node import Node
from dag.persistence import property_created_at
from dag.scheduler import get_scheduler
from dag.signals import Signal, Slot
from dag.user_input_node import UserInputNode
from houses.geopoint import GeoPoint
from houses.nodes.area import NearestTownNode, TownDescNode, TownNode, WalkabilityNode
from houses.nodes.commute_pipeline_builder import build_commute_pipeline
from houses.nodes.epc_node import CouncilTaxNode, EpcNode
from houses.nodes.equity_total_node import EquityTotalNode
from houses.nodes.geocode_node import GeocodeNode
from houses.nodes.life_insurance_total_node import LifeInsuranceTotalNode
from houses.nodes.location import BestAddressNode, BestLocationNode, PostcodeNode
from houses.nodes.monthly_mortgage_payment_node import MonthlyMortgagePaymentNode
from houses.nodes.monthly_sinking_fund_node import MonthlySinkingFundNode
from houses.nodes.mortgage_required_node import MortgageRequiredNode
from houses.nodes.schools import PrimarySchoolNode, SecondarySchoolNode
from houses.nodes.settings_node import aggregate_dict
from houses.nodes.stamp_duty_node import StampDutyNode
from houses.nodes.total_monthly_housing_cost_node import GroupMonthlyCostNode, HousingCostConfig
from houses.nodes.total_works_node import TotalWorksNode
from houses.nodes.yearly_sinking_fund_node import YearlySinkingFundNode
from houses.services_provider import get_services

if TYPE_CHECKING:
    from houses.nodes.commute import CommuteSelectorNode
    from houses.nodes.commute_breakdown_node import CommuteBreakdownNode
    from houses.services import Services


@dataclass(frozen=True)
class _PropertyJson:
    """Wire shape of PropertyNodes.to_json."""

    rid: str
    best_address: Any
    best_location: Any
    rightmove_url: Any
    rightmove_price: Any
    rightmove_bedrooms: Any
    postcode: Any

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _SchoolEntry:
    """The {school: ...} wrapper used per school stage."""

    school: Any

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return dict(school=self.school)


@dataclass(frozen=True)
class _SchoolsJson:
    """Wire shape of the schools block (primary and secondary)."""

    primary: _SchoolEntry
    secondary: _SchoolEntry

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _TriageJson:
    """Wire shape of the triage block."""

    favourite: Any
    dismissed: Any
    is_viewed: Any
    user_notes: Any
    triage_status: Any

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _FreshnessJson:
    """Wire shape of the freshness block."""

    property_added_at: str | None

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return dict(property_added_at=self.property_added_at)


@dataclass(frozen=True)
class _SummaryJson:
    """Wire shape of PropertyNodes.to_json_summary."""

    rid: str
    best_address: Any
    best_location: Any
    rightmove_price: Any
    rightmove_bedrooms: Any
    group_monthly_cost: Any
    monthly_commute_cost: Any
    town_name: Any
    commutes: dict
    schools: dict
    walkability: Any
    epc: Any
    triage: dict
    freshness: dict

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _LocationJson:
    """Wire shape of the detail location block."""

    best_location: Any
    geocode: Any
    rightmove_location: Any
    precise_location: Any

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _AffordabilityJson:
    """Wire shape of the detail affordability block."""

    stamp_duty: Any
    council_tax: Any
    works_estimates: Any
    total_works: Any
    total_equity: Any
    life_insurance_total: Any
    mortgage_required: Any
    monthly_mortgage: Any
    monthly_sinking_fund: Any
    monthly_commute_cost: Any
    rental_income: Any
    group_monthly_cost: Any

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _CouncilTaxApportionmentJson:
    """Wire shape of the council-tax apportionment block."""

    main_payers: Any
    annexe_payers: Any
    ignored: Any

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _AreaJson:
    """Wire shape of the detail area block."""

    walkability: Any
    town_description: Any

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _CommentsJson:
    """Wire shape of the detail comments block."""

    status: Any
    status_reason: Any
    group_notes: Any
    ashby_comments: Any
    design_needed: Any
    planning_needed: Any

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        return asdict(self)



@dataclass(frozen=True)
class _SettingsFinancial:
    """The financial settings block: status plus the aggregate value."""

    status: str
    value: Any

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
        return dict(status=self.status, value=self.value)


@dataclass(frozen=True)
class _SettingsBlock:
    """The settings section of the detail payload."""

    persons: Any
    financial: _SettingsFinancial

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
        return dict(persons=self.persons, financial=self.financial.to_dict())


@dataclass(frozen=True)
class _DetailJson:
    """Wire shape of PropertyNodes.to_json_detail."""

    rid: str
    best_address: Any
    user_entered_address: Any
    rightmove_url: Any
    rightmove_price: Any
    rightmove_bedrooms: Any
    town_name: Any
    epc: Any
    location: dict
    commutes: dict
    schools: dict
    affordability: dict
    council_tax_apportionment: dict
    area: dict
    triage: dict
    comments: dict
    settings: _SettingsBlock

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        d = asdict(self)
        d["settings"] = self.settings.to_dict()
        return d


class PropertyNodes:
    """Holds all DAG node references for one property.

    Creates UserInputNodes for user-owned and enrichment data, DerivedNodes
    for derived values, and wires signal propagation.
    """

    _code_refresh_epoch: int

    def __init__(self, rid: str) -> None:
        # Validate RID — must be numeric and at least 6 digits
        # (Rightmove property IDs are 6-10 digits; shorter values like
        # "999" are test data written outside pytest isolation).

        if not _dag_per.testing and (not rid.isdigit() or len(rid) < 6):
            raise ValueError(
                f"Invalid RID {rid!r}: property RIDs must be all digits and at "
                f"least 6 characters long (Rightmove IDs are 6-10 digits). "
                f"This appears to be test data."
            )
        self.rid: str = rid
        self.changed: Signal = Signal()
        self._svc: Services = get_services()

        # ── Source Nodes (user-entered / scraped) ──────────────────────
        self.rightmove_url: UserInputNode[str] = UserInputNode[str](f"{rid}/rightmove_url", str)
        self.rightmove_address: UserInputNode[str] = UserInputNode[str](f"{rid}/rightmove_address", str)
        # lucidlint: ignore duplicate-block structurally identical UserInputNode constructor calls are this file's
        self.rightmove_bedrooms: UserInputNode[str] = UserInputNode[str](f"{rid}/rightmove_bedrooms", str)
        self.rightmove_price: UserInputNode[Money] = UserInputNode[Money](f"{rid}/rightmove_price", Money)
        self.rightmove_location: UserInputNode[GeoPoint] = UserInputNode[GeoPoint](
            f"{rid}/rightmove_location", GeoPoint
        )
        self.precise_location: UserInputNode[GeoPoint] = UserInputNode[GeoPoint](f"{rid}/precise_location", GeoPoint)
        self.corrected_address: UserInputNode[str] = UserInputNode[str](f"{rid}/corrected_address", str)
        self.user_entered_address: UserInputNode[str] = UserInputNode[str](f"{rid}/user_entered_address", str)

        # Comments from View tab
        self.comment_status: UserInputNode[str] = UserInputNode[str](f"{rid}/status", str)
        self.comment_status_reason: UserInputNode[str] = UserInputNode[str](f"{rid}/status_reason", str)
        self.comment_group_notes: UserInputNode[str] = UserInputNode[str](f"{rid}/group_notes", str)
        self.comment_ashby_comments: UserInputNode[str] = UserInputNode[str](f"{rid}/ashby_comments", str)
        self.works_estimates: UserInputNode[dict[str, Money]] = UserInputNode[dict[str, Money]](
            f"{rid}/works_estimates", dict[str, Money]
        )
        self.rental_income: UserInputNode[Money] = UserInputNode[Money](f"{rid}/rental_income", Money)
        self.comment_design_needed: UserInputNode[str] = UserInputNode[str](f"{rid}/design_needed", str)
        self.comment_planning_needed: UserInputNode[str] = UserInputNode[str](f"{rid}/planning_needed", str)

        # Triage state (app-only, not synced to sheet)
        self.favourite: UserInputNode[bool] = UserInputNode[bool](f"{rid}/favourite", bool)
        self.dismissed: UserInputNode[bool] = UserInputNode[bool](f"{rid}/dismissed", bool)
        self.is_viewed: UserInputNode[bool] = UserInputNode[bool](f"{rid}/is_viewed", bool)
        self.user_notes: UserInputNode[str] = UserInputNode[str](f"{rid}/user_notes", str)
        self.triage_status: UserInputNode[str] = UserInputNode[str](f"{rid}/triage_status", str)

        # Council-tax apportionment (app-only): which people pay a share
        # of the MAIN house's council tax (empty = all adults, the
        # default headcount split) and of the ANNEXE's (if detected);
        # annexe_ignored says the second dwelling is unrelated.
        # Defaults are seeded at bootstrap so they never block refresh.
        self.council_tax_payers: UserInputNode[list[str]] = UserInputNode[list[str]](
            f"{rid}/council_tax_payers", list[str]
        )
        self.annexe_payers: UserInputNode[list[str]] = UserInputNode[list[str]](f"{rid}/annexe_payers", list[str])
        self.annexe_ignored: UserInputNode[bool] = UserInputNode[bool](f"{rid}/annexe_ignored", bool)
        # Materialise defaults NOW so the nodes are never pending: the
        # group node passes its deps to compute POSITIONALLY, so a dropped
        # pending dep would shift every later argument into the wrong
        # parameter (the main-payers value landing in annexe_payers).
        # Push only when never set — a persisted user choice survives.
        if self.council_tax_payers.latest_attempt().pending:
            self.council_tax_payers.push([], "default")
        if self.annexe_payers.latest_attempt().pending:
            self.annexe_payers.push([], "default")
        if self.annexe_ignored.latest_attempt().pending:
            self.annexe_ignored.push(value=False, source_label="default")
        self.best_address: BestAddressNode = BestAddressNode(
            f"{rid}/best_address",
            user_entered_address=self.user_entered_address,
            corrected_address=self.corrected_address,
            rightmove_address=self.rightmove_address,
        )
        self.postcode: PostcodeNode = PostcodeNode(
            f"{rid}/postcode",
            best_address=self.best_address,
        )
        self.geocode: GeocodeNode = GeocodeNode(
            f"{rid}/geocode",
            best_address=self.best_address,
        )
        self.best_location: BestLocationNode = BestLocationNode(
            f"{rid}/best_location",
            precise_location=self.precise_location,
            rightmove_location=self.rightmove_location,
            best_address=self.best_address,
            geocode=self.geocode,
        )

        # ── Enrichment Nodes ────────────────────────────────────────────
        self.epc: EpcNode = EpcNode(
            f"{rid}/epc",
            best_address=self.best_address,
            postcode_node=self.postcode,
        )
        self.council_tax: CouncilTaxNode = CouncilTaxNode(
            f"{rid}/council_tax",
            best_address=self.best_address,
            postcode_node=self.postcode,
        )
        self.walkability: WalkabilityNode = WalkabilityNode(
            f"{rid}/walkability",
            best_location=self.best_location,
            best_address=self.best_address,
        )
        self.nearest_town: NearestTownNode = NearestTownNode(
            f"{rid}/nearest_town",
            best_location=self.best_location,
        )
        self.town_name: TownNode = TownNode(
            f"{rid}/town_name",
            best_address=self.best_address,
        )
        self.town_desc: TownDescNode = TownDescNode(
            f"{rid}/town_desc_v3",
            best_location=self.best_location,
            nearest_town=self.nearest_town,
            town_name=self.town_name,
            postcode_node=self.postcode,
        )

        # ── School Nodes ───────────────────────────────────────────────
        # Find the first child person's acceptable school types
        _school_acceptable = ("mixed",)
        for p in self._svc.persons_source._value or []:
            if p.is_child:
                _school_acceptable = p.acceptable_schools
                break
        self.primary_school: PrimarySchoolNode = PrimarySchoolNode(
            f"{rid}/primary_school",
            best_location=self.best_location,
            best_address=self.best_address,
            acceptable=_school_acceptable,
        )
        self.secondary_school: SecondarySchoolNode = SecondarySchoolNode(
            f"{rid}/secondary_school",
            best_location=self.best_location,
            best_address=self.best_address,
            acceptable=_school_acceptable,
        )
        # ── Commute Pipeline ────────────────────────────────────────────
        # The builder attaches these in place; declare them so pyrefly
        # knows the attributes exist before the helper assigns them.
        self.commute_selectors: dict[str, CommuteSelectorNode] = {}
        self.commute_breakdown: CommuteBreakdownNode | None = None
        self._build_commute_pipeline()
        # The builder always attaches the breakdown node; narrow the
        # Optional declaration for the config wiring below.
        assert self.commute_breakdown is not None, "commute pipeline not built"

        # ── Monthly Cost Calculation Nodes ──────────────────────────────
        self.stamp_duty: StampDutyNode = StampDutyNode(
            f"{rid}/stamp_duty",
            rightmove_price=self.rightmove_price,
            status_node=self.comment_status,
        )
        # ── Works Estimates ───────────────────────────────────────────
        self.total_works: TotalWorksNode = TotalWorksNode(
            f"{rid}/total_works",
            persons_source=self._svc.persons_source,
            works_estimates_node=self.works_estimates,
        )
        self.total_equity: EquityTotalNode = EquityTotalNode(
            f"{rid}/total_equity",
            persons_source=self._svc.persons_source,
            status_node=self.comment_status,
        )
        self.life_insurance_total: LifeInsuranceTotalNode = LifeInsuranceTotalNode(
            f"{rid}/life_insurance_total",
            persons_source=self._svc.persons_source,
        )
        self.mortgage_required: MortgageRequiredNode = MortgageRequiredNode(
            f"{rid}/mortgage_required",
            rightmove_price=self.rightmove_price,
            stamp_duty=self.stamp_duty,
            total_works_node=self.total_works,
            total_equity_node=self.total_equity,
        )
        self.monthly_mortgage: MonthlyMortgagePaymentNode = MonthlyMortgagePaymentNode(
            f"{rid}/monthly_mortgage",
            mortgage_required_node=self.mortgage_required,
            mortgage_rate_node=self._svc.setting_nodes.get("settings/mortgage_rate"),
            mortgage_term_node=self._svc.setting_nodes.get("settings/mortgage_term"),
        )
        self.yearly_sinking_fund: YearlySinkingFundNode = YearlySinkingFundNode(
            f"{rid}/yearly_sinking_fund",
            rightmove_price=self.rightmove_price,
            sinking_fund_rate_node=self._svc.setting_nodes.get("settings/sinking_fund_rate"),
        )
        self.monthly_sinking_fund: MonthlySinkingFundNode = MonthlySinkingFundNode(
            f"{rid}/monthly_sinking_fund",
            yearly_sinking_fund_node=self.yearly_sinking_fund,
        )

        # The headline has NO family total — only the joint owners'
        # (couple) figure and the other adults' figure, labelled by name.
        self.group_monthly_cost: GroupMonthlyCostNode = GroupMonthlyCostNode(
            f"{rid}/group_monthly_cost",
            config=HousingCostConfig(
                monthly_mortgage_node=self.monthly_mortgage,
                yearly_sinking_fund_node=self.yearly_sinking_fund,
                life_insurance_node=self.life_insurance_total,
                rental_income_node=self.rental_income,
                status_node=self.comment_status,
                commute_breakdown_node=self.commute_breakdown,
                council_tax_node=self.council_tax,
                persons_source=self._svc.persons_source,
                annexe_payers_node=self.annexe_payers,
                annexe_ignored_node=self.annexe_ignored,
                council_tax_payers_node=self.council_tax_payers,
            ),
        )

        # ── Signal wiring ──────────────────────────────────────────────
        # Wire every Node to PropertyNodes.changed so the frontend
        # is notified via WebSocket whenever any value changes.
        # Automatic — no manual list to keep in sync.
        self._slots: list[Slot] = []
        for attr in dir(self):
            node = getattr(self, attr, None)
            if isinstance(node, Node):
                slot = Slot(self._on_node_changed)
                self._slots.append(slot)
                node.changed.connect(slot)

    def _build_commute_pipeline(self) -> None:

        build_commute_pipeline(self)

    def _on_node_changed(self) -> None:
        self.changed.emit()

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    async def to_json(self) -> dict[str, Any]:
        rec = _PropertyJson(
            rid=self.rid,
            best_address=await self.best_address.to_json(),
            best_location=await self.best_location.to_json(),
            rightmove_url=await self.rightmove_url.to_json(),
            rightmove_price=await self.rightmove_price.to_json(),
            rightmove_bedrooms=await self.rightmove_bedrooms.to_json(),
            postcode=await self.postcode.to_json(),
        )
        return rec.to_dict()

    def schedule_code_stale_nodes(self) -> None:
        """Schedule (never await) recompute for derived nodes whose
        persisted result was produced by different code.

        PRD contract: reads and writes are non-blocking — a read must
        never walk the graph, and every recompute is scheduled by
        whatever makes it necessary (a dependency write, or a deploy
        invalidating persisted fingerprints).  The queue dedupes by node
        id and drains in the background.

        The walk is O(1) between code changes: the per-class fingerprint
        cache only grows when CODE changes, so the module epoch tells us
        nothing could have become stale.
        """
        last = getattr(self, "_code_refresh_epoch", None)
        if last == _dag_derived._CODE_VERSION_EPOCH:
            return

        # Walk the whole node graph via deps — vars(self) alone misses
        # nodes stored in containers (the commute selectors dict and
        # their sub-pipeline are only reachable through deps).
        seen: set[int] = set()
        queue = [n for n in vars(self).values() if isinstance(n, Node)]
        while queue:
            node = queue.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            if isinstance(node, DerivedNode):
                if node.code_is_stale():
                    get_scheduler().schedule(node)
                queue.extend(node._get_active_deps())
        self._code_refresh_epoch = _dag_derived._CODE_VERSION_EPOCH


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    async def _commute_breakdown_json(self) -> dict:
        """The commute aggregator is attached by the pipeline builder during
        __init__ — it is always present by the time serialization runs."""
        assert self.commute_breakdown is not None, "commute pipeline not built"
        return await self.commute_breakdown.to_json()

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    async def to_json_summary(self) -> dict[str, Any]:
        triage = _TriageJson(
            favourite=await self.favourite.to_json_value(),
            dismissed=await self.dismissed.to_json_value(),
            is_viewed=await self.is_viewed.to_json_value(),
            user_notes=await self.user_notes.to_json_value(),
            triage_status=await self.triage_status.to_json_value(),
        )
        schools = _SchoolsJson(
            primary=_SchoolEntry(school=await self.primary_school.to_json_value()),
            secondary=_SchoolEntry(school=await self.secondary_school.to_json_value()),
        )
        rec = _SummaryJson(
            rid=self.rid,
            best_address=await self.best_address.to_json_value(),
            best_location=await self.best_location.to_json_value(),
            rightmove_price=await self.rightmove_price.to_json_value(),
            rightmove_bedrooms=await self.rightmove_bedrooms.to_json_value(),
            group_monthly_cost=await self.group_monthly_cost.to_json_value(),
            monthly_commute_cost=await self._commute_breakdown_json(),
            town_name=await self.town_name.to_json_value(),
            commutes={k: {"commute": await v.to_json_value()} for k, v in self.commute_selectors.items()},
            schools=schools.to_dict(),
            walkability=await self.walkability.to_json_value(),
            epc=await self.epc.to_json_value(),
            triage=triage.to_dict(),
            freshness=_FreshnessJson(property_added_at=property_created_at(self.rid)).to_dict(),
        )
        return rec.to_dict()

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    async def to_json_detail(self) -> dict[str, Any]:
        location = _LocationJson(
            best_location=await self.best_location.to_json(),
            geocode=await self.geocode.to_json(),
            rightmove_location=await self.rightmove_location.to_json(),
            precise_location=await self.precise_location.to_json(),
        )
        schools = _SchoolsJson(
            primary=_SchoolEntry(school=await self.primary_school.to_json()),
            secondary=_SchoolEntry(school=await self.secondary_school.to_json()),
        )
        affordability = _AffordabilityJson(
            stamp_duty=await self.stamp_duty.to_json(),
            council_tax=await self.council_tax.to_json(),
            works_estimates=await self.works_estimates.to_json(),
            total_works=await self.total_works.to_json(),
            total_equity=await self.total_equity.to_json(),
            life_insurance_total=await self.life_insurance_total.to_json(),
            mortgage_required=await self.mortgage_required.to_json(),
            monthly_mortgage=await self.monthly_mortgage.to_json(),
            monthly_sinking_fund=await self.monthly_sinking_fund.to_json(),
            monthly_commute_cost=await self._commute_breakdown_json(),
            rental_income=await self.rental_income.to_json(),
            group_monthly_cost=await self.group_monthly_cost.to_json(),
        )
        apportionment = _CouncilTaxApportionmentJson(
            main_payers=await self.council_tax_payers.to_json_value(),
            annexe_payers=await self.annexe_payers.to_json_value(),
            ignored=await self.annexe_ignored.to_json_value(),
        )
        area = _AreaJson(
            walkability=await self.walkability.to_json(),
            town_description=await self.town_desc.to_json(),
        )
        triage = _TriageJson(
            favourite=await self.favourite.to_json(),
            dismissed=await self.dismissed.to_json(),
            is_viewed=await self.is_viewed.to_json(),
            user_notes=await self.user_notes.to_json(),
            triage_status=await self.triage_status.to_json(),
        )
        comments = _CommentsJson(
            status=await self.comment_status.to_json(),
            status_reason=await self.comment_status_reason.to_json(),
            group_notes=await self.comment_group_notes.to_json(),
            ashby_comments=await self.comment_ashby_comments.to_json(),
            design_needed=await self.comment_design_needed.to_json(),
            planning_needed=await self.comment_planning_needed.to_json(),
        )
        rec = _DetailJson(
            rid=self.rid,
            best_address=await self.best_address.to_json(),
            user_entered_address=await self.user_entered_address.to_json(),
            rightmove_url=await self.rightmove_url.to_json(),
            rightmove_price=await self.rightmove_price.to_json(),
            rightmove_bedrooms=await self.rightmove_bedrooms.to_json(),
            town_name=await self.town_name.to_json(),
            epc=await self.epc.to_json(),
            location=location.to_dict(),
            commutes={k: await v.to_json() for k, v in self.commute_selectors.items()},
            schools=schools.to_dict(),
            affordability=affordability.to_dict(),
            council_tax_apportionment=apportionment.to_dict(),
            area=area.to_dict(),
            triage=triage.to_dict(),
            comments=comments.to_dict(),
            settings=_SettingsBlock(
                persons=await self._svc.persons_source.to_json(),
                financial=_SettingsFinancial(
                    status="succeeded", value=aggregate_dict(self._svc.setting_nodes)
                ),
            ),
        )
        return rec.to_dict()
