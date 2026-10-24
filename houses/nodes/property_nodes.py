from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from money import Money

import dag.derived_node as _dag_derived
import dag.persistence as _dag_per
from dag.derived_node import DerivedNode
from dag.node import Node
from dag.regenerate import schedule_code_stale_nodes
from dag.scheduler import get_scheduler
from dag.signal import Signal
from dag.slot import Slot
from dag.user_input_node import UserInputNode
from houses.geopoint import GeoPoint
from houses.model.domain import person_id_of
from houses.nodes.best_address_node import BestAddressNode
from houses.nodes.best_location_node import BestLocationNode
from houses.nodes.commute_breakdown_node import CommuteBreakdownNode
from houses.nodes.commute_pipeline_builder import build_commute_pipeline
from houses.nodes.current_home_node import current_home_node
from houses.nodes.delta_vs_home_node import DeltaVsHomeNode
from houses.nodes.epc_node import CouncilTaxNode, EpcNode
from houses.nodes.equity_total_node import EquityTotalNode
from houses.nodes.geocode_node import GeocodeNode
from houses.nodes.life_insurance_total_node import LifeInsuranceTotalNode
from houses.nodes.monthly_mortgage_payment_node import MonthlyMortgagePaymentNode
from houses.nodes.monthly_sinking_fund_node import MonthlySinkingFundNode
from houses.nodes.mortgage_required_node import MortgageRequiredNode
from houses.nodes.nearest_school_node import NearestSchoolNode
from houses.nodes.nearest_town_node import NearestTownNode
from houses.nodes.postcode_node import PostcodeNode
from houses.nodes.property_detail import PropertyDetail, property_detail
from houses.nodes.property_summary import PropertySummary, property_summary
from houses.nodes.property_view import PropertyView
from houses.nodes.school_acceptance_node import SchoolAcceptanceNode
from houses.nodes.stamp_duty_node import StampDutyNode
from houses.nodes.total_monthly_housing_cost_node import GroupMonthlyCostNode, HousingCostConfig
from houses.nodes.total_works_node import TotalWorksNode
from houses.nodes.town_desc_node import TownDescNode
from houses.nodes.town_node import TownNode
from houses.nodes.walkability_node import WalkabilityNode
from houses.nodes.yearly_sinking_fund_node import YearlySinkingFundNode
from houses.services_provider import get_services

if TYPE_CHECKING:
    from houses.services import Services

# A node's provenance tree as the serve path returns it (Provenance.to_dict):
# label/value/sources, recursive and mostly optional, so it is named here
# rather than modelled field by field.
type ProvenanceTree = dict[str, Any]

# Dotted-path → provenance-tree map served on demand (the detail payload's
# node layout is the key contract — variable keys, one tree per path).
type ProvenanceMap = dict[str, ProvenanceTree]

# Node attribute → factory for an input whose empty state is a valid fact
# (the construction invariant materialises them; see _EMPTY_VALID_INPUT_DEFAULTS).
type _EmptyValidDefaults = dict[str, Callable[[], Any]]


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

        if not _dag_per.Runtime.testing and (not rid.isdigit() or len(rid) < 6):
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
        self.rightmove_bedrooms: UserInputNode[str] = UserInputNode[str](f"{rid}/rightmove_bedrooms", str)
        # Every line of this UserInputNode wiring table wires a distinct node key and value type —
        # lucidlint: ignore duplicate-block extracting a helper/loop was probed and re-fires or drops the types
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
            nearest_town=self.nearest_town,
            town_name=self.town_name,
            postcode_node=self.postcode,
        )

        # ── School Nodes ───────────────────────────────────────────────
        # Acceptable school types are a PERSONS-SOURCED DEP (Part F
        # 2.2): a filter edit changes the dep, the school nodes recompute
        # — never a constructor snapshot frozen at property build.
        self.school_acceptable: SchoolAcceptanceNode = SchoolAcceptanceNode(
            f"{rid}/school_acceptable",
            persons_source=self._svc.persons_source,
        )
        self.primary_school: NearestSchoolNode = NearestSchoolNode(
            f"{rid}/primary_school",
            best_location=self.best_location,
            child_age=4,
            stage="primary",
            acceptable=self.school_acceptable,
        )
        self.secondary_school: NearestSchoolNode = NearestSchoolNode(
            f"{rid}/secondary_school",
            best_location=self.best_location,
            child_age=12,
            stage="secondary",
            acceptable=self.school_acceptable,
        )
        # ── Commute Pipeline ────────────────────────────────────────────
        # Pipelines are materialized for the initial destination set,
        # and the breakdown node is created ONCE with the live selectors
        # as its deps (nodes, never a provider closure). _on_persons_changed
        # adds/removes pipelines and rewires via set_deps; the breakdown
        # object is stable for the property's lifetime.
        self.commute_selectors: dict[str, DerivedNode] = {}
        self.commute_pois: dict[str, UserInputNode[str]] = {}
        self._build_commute_pipeline()
        self.commute_breakdown: CommuteBreakdownNode = CommuteBreakdownNode(
            f"{rid}/commute_breakdown",
            selectors=tuple(self.commute_selectors.values()),
            persons_source=self._svc.persons_source,
        )
        self._svc.persons_source.changed.connect(self._on_persons_changed)

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

        # The monthly "vs your home" comparison — a DAG node like every
        # other calculation (docs/dag-library.md → Design Rules): deps are
        # own group cost + the global current-home node; the subtraction,
        # its wire value and its provenance are all the DAG's own record.
        self.delta_vs_home: DeltaVsHomeNode = DeltaVsHomeNode(
            f"{rid}/delta_vs_home",
            group_node=self.group_monthly_cost,
            current_home=current_home_node(),
        )

        # ── Signal wiring ──────────────────────────────────────────────
        # Wire every Node to PropertyNodes.changed so the frontend
        # is notified via WebSocket whenever any value changes.
        # Automatic — no manual list to keep in sync.
        self._slots: list[Slot] = []
        for attr in dir(self):
            node = getattr(self, attr, None)
            if isinstance(node, Node):
                # Every node this container builds is a property-view
                # node: declare the role and the owning property at
                # construction, so the DAG→frontend seam routes and
                # coalesces without parsing node ids.
                node.refresh_kind = "property"
                node.property_rid = self.rid
                slot = Slot(self._on_node_changed)
                self._slots.append(slot)
                node.changed.connect(slot)

        # ── Construction invariant: no pending empty-valid inputs ──────
        # A pending input node permanently blocks every downstream
        # refresh (equity → mortgage → monthly payment), which is how
        # add-flow houses lost their Cost of Works rows until the next
        # restart (2026-09-06). Inputs whose "empty" state is a valid
        # fact are materialised HERE, at construction, so no creation
        # path can skip it. Scrape-produced inputs (address, price,
        # bedrooms, location) deliberately stay pending until the
        # scrape lands.
        for attr, make_default in self._EMPTY_VALID_INPUT_DEFAULTS.items():
            node = getattr(self, attr, None)
            if node is None:
                continue
            attempt = node.latest_attempt()
            if attempt is None or attempt.pending:
                node.push(make_default(), "default")

    # Empty-valid input defaults, kept next to the node definitions.
    # A new user-input node with an "empty is valid" semantic must be
    # registered here.
    _EMPTY_VALID_INPUT_DEFAULTS: _EmptyValidDefaults = {
        attr: make_default
        for attr, make_default in (
            ("comment_status", lambda: ""),
            ("comment_status_reason", lambda: ""),
            ("works_estimates", dict),
            ("rental_income", lambda: Money(amount="0", currency="GBP")),
        )
    }

    def _build_commute_pipeline(self) -> None:

        build_commute_pipeline(self)

    def _on_persons_changed(self) -> None:
        """The ONE invalidation point for the commute aggregate: every
        persons push re-prices the breakdown through it.

        Destination-set changes materialize or disconnect individual
        pipelines — the selectors dict is mutated IN PLACE and the
        breakdown node is never replaced, because the household-cost
        node wired itself to the breakdown object at construction (the
        live 0.0-commutes regression). A moved destination (same key,
        new address) pushes the new address into its pipeline's poi
        node, so routes re-plan and the congestion-zone gate
        re-evaluates — all through the DAG's own change mechanics.
        Value-only changes (trips/car/MPG) need no structural work at
        all. Runs on the DAG processor thread (persons pushes land
        there)."""
        wanted = {
            f"{person_id_of(p)}/{q.label}"
            for p in (self._svc.persons_source._value or [])
            for q in (p.places_of_interest or [])
        }
        current = set(self.commute_selectors)

        # Teardown pipelines whose destination was removed: disconnect
        # every node under the removed keys (drops scheduler registration
        # and signal slots), then drop the key from the selectors dict —
        # the breakdown reads that dict by reference.
        removed = current - set(wanted)
        for key in removed:
            prefix = f"{self.rid}/{key}/"
            for nid, node in list(get_scheduler().registered_nodes().items()):
                if nid.startswith(prefix):
                    node.disconnect()
            self.commute_selectors.pop(key, None)

        # Materialize pipelines for newly added destinations, then
        # rewire the breakdown's deps to the live selector entries
        # plus persons_source — deps are nodes, so each selector write
        # signals the breakdown through its own dep slot. No manual
        # scheduling: the deps define who gets updated when.
        build_commute_pipeline(self, keys=set(wanted) - current)
        self.commute_breakdown.set_deps((*self.commute_selectors.values(), self._svc.persons_source))

    def _on_node_changed(self) -> None:
        self.changed.emit()

    async def view(self) -> PropertyView:
        rec = PropertyView(
            rid=self.rid,
            best_address=await self.best_address.to_json(),
            best_location=await self.best_location.to_json(),
            rightmove_url=await self.rightmove_url.to_json(),
            rightmove_price=await self.rightmove_price.to_json(),
            rightmove_bedrooms=await self.rightmove_bedrooms.to_json(),
            postcode=await self.postcode.to_json(),
        )
        return rec

    def schedule_code_stale_nodes(self) -> None:
        """Schedule (never await) recompute for derived nodes whose
        persisted result was produced by different code.

        PRD contract: reads and writes are non-blocking — a read must
        never walk the graph, and every recompute is scheduled by
        whatever makes it necessary (a dependency write, or a deploy
        re-stamping a referenced class's fingerprint).  The queue dedupes
        by node id and drains in the background.

        The walk is O(1) between code changes: the per-class fingerprint
        cache only grows when CODE changes, so the module epoch tells us
        nothing could have become stale.
        """
        last = getattr(self, "_code_refresh_epoch", None)
        if last == _dag_derived.DerivedNode._CODE_VERSION_EPOCH:
            return

        # Walk the whole node graph — vars(self) alone misses nodes stored
        # in containers (the commute selectors dict and their sub-pipeline
        # are only reachable through deps).  The walk follows every
        # dependency, including the ones a node's active set hides: the
        # commute wrapper drops a FAILED pipeline from its active deps so
        # the failure cannot propagate, and a refresh that inherited that
        # narrowing could never reach the pipeline it must refresh.
        schedule_code_stale_nodes(n for n in vars(self).values() if isinstance(n, Node))
        self._code_refresh_epoch = _dag_derived.DerivedNode._CODE_VERSION_EPOCH

    async def commute_breakdown_json(self) -> Mapping[str, Any]:
        """The commute aggregator is attached by the pipeline builder during
        __init__ — it is always present by the time serialization runs."""
        return await self.commute_breakdown.to_json_value()

    def commuted_destinations(self) -> set[str]:
        """Selector keys whose destination is actually commuted (trips
        and weeks both > 0). A destination that does not happen — zero
        days a week, or zero weeks a year — takes no pill and no cost
        entry. The cost calculation itself just multiplies; this filter
        is presentational (the index card's pills)."""
        return {
            f"{person_id_of(p)}/{q.label}"
            for p in (self._svc.persons_source._value or [])
            for q in (p.places_of_interest or [])
            if (q.trips_per_week or 0) > 0 and (q.weeks_per_year or 0) > 0
        }
    @staticmethod
    def commute_wire_key(key: str, name_by_id: dict[str, str]) -> str:
        """Selector keys are id/label; the WIRE keys stay name/label —
        names are display, ids never leak to the frontend."""
        pid, _, label = key.partition("/")
        return f"{name_by_id.get(pid, pid)}/{label}"

    def commute_wire_map(self) -> dict[str, str]:
        """pid → display name for the serialization key projection."""
        return {
            person_id_of(p): getattr(p, "name", "?")
            for p in (self._svc.persons_source._value or [])
        }

    async def summary(self) -> PropertySummary:
        return await property_summary(self)

    async def detail(self) -> PropertyDetail:
        return await property_detail(self, self._svc)


    async def to_provenance_map(self) -> ProvenanceMap:
        """Provenance for every detail-surface node, keyed by dotted path.

        The detail wire carries no provenance (P8: a derivation is
        revealed only when the user asks). The trees are served HERE, on
        demand, from the persisted rows (serve path — no recompute).
        Dotted paths mirror the detail payload's node layout, and are the
        contract the frontend's ProvenanceToggle looks up.
        """
        async def _prov(node) -> ProvenanceTree:
            return (await node.build_provenance()).to_dict()

        pairs: list[tuple[str, Any]] = [
            ("location.best_location", self.best_location),
            ("location.geocode", self.geocode),
            ("location.rightmove_location", self.rightmove_location),
            ("location.precise_location", self.precise_location),
            ("schools.primary", self.primary_school),
            ("schools.secondary", self.secondary_school),
            ("affordability.stamp_duty", self.stamp_duty),
            ("affordability.council_tax", self.council_tax),
            ("affordability.works_estimates", self.works_estimates),
            ("affordability.total_works", self.total_works),
            ("affordability.total_equity", self.total_equity),
            ("affordability.life_insurance_total", self.life_insurance_total),
            ("affordability.mortgage_required", self.mortgage_required),
            ("affordability.monthly_mortgage", self.monthly_mortgage),
            ("affordability.monthly_sinking_fund", self.monthly_sinking_fund),
            ("affordability.rental_income", self.rental_income),
            ("affordability.monthly_commute_cost", self.commute_breakdown),
            ("affordability.group_monthly_cost", self.group_monthly_cost),
            ("area.walkability", self.walkability),
            ("area.town_description", self.town_desc),
            ("best_address", self.best_address),
            ("user_entered_address", self.user_entered_address),
            ("rightmove_url", self.rightmove_url),
            ("rightmove_price", self.rightmove_price),
            ("rightmove_bedrooms", self.rightmove_bedrooms),
            ("town_name", self.town_name),
            ("epc", self.epc),
            ("settings.persons", self._svc.persons_source),
        ]
        result: ProvenanceMap = {}
        for path, node in pairs:
            result[path] = await _prov(node)
        for key, selector in self.commute_selectors.items():
            if key in self.commuted_destinations():
                result[f"commutes.{self.commute_wire_key(key, self.commute_wire_map())}"] = await _prov(selector)
        return result
