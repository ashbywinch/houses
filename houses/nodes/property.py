from __future__ import annotations

from typing import Any

from money import Money

from dag.node import Node
from dag.signals import Signal, Slot
from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint
from houses.nodes.area import NearestTownNode, TownDescNode, TownNode, WalkabilityNode
from houses.nodes.epc_node import CouncilTaxNode, EpcNode
from houses.nodes.equity_total_node import EquityTotalNode
from houses.nodes.geocode import GeocodeNode
from houses.nodes.life_insurance_node import LifeInsuranceTotalNode
from houses.nodes.location import BestAddressNode, BestLocationNode
from houses.nodes.monthly_mortgage_payment_node import MonthlyMortgagePaymentNode
from houses.nodes.monthly_sinking_fund_node import MonthlySinkingFundNode
from houses.nodes.mortgage_required_node import MortgageRequiredNode
from houses.nodes.schools import PrimarySchoolNode, SecondarySchoolNode
from houses.nodes.settings_node import aggregate_dict
from houses.nodes.stamp_duty_node import StampDutyNode
from houses.nodes.total_works_node import TotalWorksNode
from houses.nodes.yearly_sinking_fund_node import YearlySinkingFundNode
from houses.services_provider import get_services


class PropertyNodes:
    """Holds all DAG node references for one property.

    Creates UserInputNodes for user-owned and enrichment data, DerivedNodes
    for derived values, and wires signal propagation.
    """

    def __init__(self, rid: str) -> None:
        # Validate RID — must be numeric and at least 6 digits
        # (Rightmove property IDs are 6-10 digits; shorter values like
        # "999" are test data written outside pytest isolation).
        import dag.persistence as _dag_per

        if not _dag_per.testing and (not rid.isdigit() or len(rid) < 6):
            raise ValueError(
                f"Invalid RID {rid!r}: property RIDs must be all digits and at "
                f"least 6 characters long (Rightmove IDs are 6-10 digits). "
                f"This appears to be test data."
            )
        self.rid = rid
        self.changed = Signal()
        self._svc = get_services()

        # ── Source Nodes (user-entered / scraped) ──────────────────────
        self.rightmove_url = UserInputNode[str](f"{rid}/rightmove_url", str)
        self.rightmove_address = UserInputNode[str](f"{rid}/rightmove_address", str)
        self.rightmove_bedrooms = UserInputNode[str](f"{rid}/rightmove_bedrooms", str)
        self.rightmove_price = UserInputNode[Money](f"{rid}/rightmove_price", Money)
        self.rightmove_location = UserInputNode[GeoPoint](f"{rid}/rightmove_location", GeoPoint)
        self.precise_location = UserInputNode[GeoPoint](f"{rid}/precise_location", GeoPoint)
        self.corrected_address = UserInputNode[str](f"{rid}/corrected_address", str)
        self.user_entered_address = UserInputNode[str](f"{rid}/user_entered_address", str)
        self.postcode = UserInputNode[str](f"{rid}/postcode", str)
        self.actual_postcode = UserInputNode[str](f"{rid}/actual_postcode", str)

        # Comments from View tab
        self.comment_status = UserInputNode[str](f"{rid}/status", str)
        self.comment_status_reason = UserInputNode[str](f"{rid}/status_reason", str)
        self.comment_group_notes = UserInputNode[str](f"{rid}/group_notes", str)
        self.comment_ashby_comments = UserInputNode[str](f"{rid}/ashby_comments", str)
        self.works_estimates = UserInputNode[dict[str, Money]](f"{rid}/works_estimates", dict[str, Money])
        self.rental_income = UserInputNode[Money](f"{rid}/rental_income", Money)
        self.comment_design_needed = UserInputNode[str](f"{rid}/design_needed", str)
        self.comment_planning_needed = UserInputNode[str](f"{rid}/planning_needed", str)

        # Triage state (app-only, not synced to sheet)
        self.favourite = UserInputNode[bool](f"{rid}/favourite", bool)
        self.dismissed = UserInputNode[bool](f"{rid}/dismissed", bool)
        self.is_viewed = UserInputNode[bool](f"{rid}/is_viewed", bool)
        self.user_notes = UserInputNode[str](f"{rid}/user_notes", str)
        self.triage_status = UserInputNode[str](f"{rid}/triage_status", str)

        # Annexe apportionment (app-only, not synced to sheet): which
        # people pay a share of the annexe's council tax, and whether the
        # detected second dwelling is actually unrelated to the purchase.
        # Defaults are seeded at bootstrap so they never block refresh.
        self.annexe_payers = UserInputNode[list[str]](f"{rid}/annexe_payers", list[str])
        self.annexe_ignored = UserInputNode[bool](f"{rid}/annexe_ignored", bool)
        # ── Location DerivedNodes ─────────────────────────────────────
        self.best_address = BestAddressNode(
            f"{rid}/best_address",
            user_entered_address=self.user_entered_address,
            corrected_address=self.corrected_address,
            rightmove_address=self.rightmove_address,
        )
        self.geocode = GeocodeNode(
            f"{rid}/geocode",
            best_address=self.best_address,
        )
        self.best_location = BestLocationNode(
            f"{rid}/best_location",
            precise_location=self.precise_location,
            rightmove_location=self.rightmove_location,
            best_address=self.best_address,
            geocode=self.geocode,
        )

        # ── Enrichment Nodes ────────────────────────────────────────────
        self.epc = EpcNode(
            f"{rid}/epc",
            best_address=self.best_address,
            postcode_node=self.postcode,
        )
        self.council_tax = CouncilTaxNode(
            f"{rid}/council_tax",
            best_address=self.best_address,
            postcode_node=self.postcode,
        )
        self.walkability = WalkabilityNode(
            f"{rid}/walkability",
            best_location=self.best_location,
            best_address=self.best_address,
        )
        self.nearest_town = NearestTownNode(
            f"{rid}/nearest_town",
            best_location=self.best_location,
        )
        self.town_name = TownNode(
            f"{rid}/town_name",
            best_address=self.best_address,
        )
        self.town_desc = TownDescNode(
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
        self.primary_school = PrimarySchoolNode(
            f"{rid}/primary_school",
            best_location=self.best_location,
            best_address=self.best_address,
            acceptable=_school_acceptable,
        )
        self.secondary_school = SecondarySchoolNode(
            f"{rid}/secondary_school",
            best_location=self.best_location,
            best_address=self.best_address,
            acceptable=_school_acceptable,
        )
        # ── Commute Pipeline ────────────────────────────────────────────
        self._build_commute_pipeline()

        # ── Monthly Cost Calculation Nodes ──────────────────────────────
        self.stamp_duty = StampDutyNode(
            f"{rid}/stamp_duty",
            rightmove_price=self.rightmove_price,
            status_node=self.comment_status,
        )
        # ── Works Estimates ───────────────────────────────────────────
        self.total_works = TotalWorksNode(
            f"{rid}/total_works",
            persons_source=self._svc.persons_source,
            works_estimates_node=self.works_estimates,
        )
        self.total_equity = EquityTotalNode(
            f"{rid}/total_equity",
            persons_source=self._svc.persons_source,
            status_node=self.comment_status,
        )
        self.life_insurance_total = LifeInsuranceTotalNode(
            f"{rid}/life_insurance_total",
            persons_source=self._svc.persons_source,
        )
        self.mortgage_required = MortgageRequiredNode(
            f"{rid}/mortgage_required",
            rightmove_price=self.rightmove_price,
            stamp_duty=self.stamp_duty,
            total_works_node=self.total_works,
            total_equity_node=self.total_equity,
        )
        self.monthly_mortgage = MonthlyMortgagePaymentNode(
            f"{rid}/monthly_mortgage",
            mortgage_required_node=self.mortgage_required,
            mortgage_rate_node=self._svc.setting_nodes.get("settings/mortgage_rate"),
            mortgage_term_node=self._svc.setting_nodes.get("settings/mortgage_term"),
        )
        self.yearly_sinking_fund = YearlySinkingFundNode(
            f"{rid}/yearly_sinking_fund",
            rightmove_price=self.rightmove_price,
            sinking_fund_rate_node=self._svc.setting_nodes.get("settings/sinking_fund_rate"),
        )
        self.monthly_sinking_fund = MonthlySinkingFundNode(
            f"{rid}/monthly_sinking_fund",
            yearly_sinking_fund_node=self.yearly_sinking_fund,
        )
        from houses.nodes.total_monthly_housing_cost_node import GroupMonthlyCostNode

        # The headline has NO family total — only the joint owners'
        # (couple) figure and the other adults' figure, labelled by name.
        self.group_monthly_cost = GroupMonthlyCostNode(
            f"{rid}/group_monthly_cost",
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
        from houses.nodes.commute_pipeline_builder import build_commute_pipeline

        build_commute_pipeline(self)

    def _on_node_changed(self) -> None:
        self.changed.emit()

    async def to_json(self) -> dict[str, Any]:
        return {
            "rid": self.rid,
            "best_address": await self.best_address.to_json(),
            "best_location": await self.best_location.to_json(),
            "rightmove_url": await self.rightmove_url.to_json(),
            "rightmove_price": await self.rightmove_price.to_json(),
            "rightmove_bedrooms": await self.rightmove_bedrooms.to_json(),
            "postcode": await self.postcode.to_json(),
        }

    async def to_json_summary(self) -> dict[str, Any]:
        from dag.persistence import property_created_at

        result = {
            "rid": self.rid,
            "best_address": await self.best_address.to_json_value(),
            "best_location": await self.best_location.to_json_value(),
            "rightmove_price": await self.rightmove_price.to_json_value(),
            "rightmove_bedrooms": await self.rightmove_bedrooms.to_json_value(),
            "group_monthly_cost": await self.group_monthly_cost.to_json_value(),
            "town_name": await self.town_name.to_json_value(),
            "commutes": {k: {"commute": await v.to_json_value()} for k, v in self.commute_selectors.items()},
            "schools": {
                "primary": {
                    "school": await self.primary_school.to_json_value(),
                },
                "secondary": {
                    "school": await self.secondary_school.to_json_value(),
                },
            },
            "walkability": await self.walkability.to_json_value(),
            "epc": await self.epc.to_json_value(),
            "triage": {
                "favourite": await self.favourite.to_json_value(),
                "dismissed": await self.dismissed.to_json_value(),
                "is_viewed": await self.is_viewed.to_json_value(),
                "user_notes": await self.user_notes.to_json_value(),
                "triage_status": await self.triage_status.to_json_value(),
            },
        }
        result["freshness"] = {
            "property_added_at": property_created_at(self.rid),
        }
        return result

    async def to_json_detail(self) -> dict[str, Any]:
        return {
            "rid": self.rid,
            "best_address": await self.best_address.to_json(),
            "user_entered_address": await self.user_entered_address.to_json(),
            "rightmove_url": await self.rightmove_url.to_json(),
            "rightmove_price": await self.rightmove_price.to_json(),
            "rightmove_bedrooms": await self.rightmove_bedrooms.to_json(),
            "town_name": await self.town_name.to_json(),
            "epc": await self.epc.to_json(),
            "location": {
                "best_location": await self.best_location.to_json(),
                "geocode": await self.geocode.to_json(),
                "rightmove_location": await self.rightmove_location.to_json(),
                "precise_location": await self.precise_location.to_json(),
            },
            "commutes": {k: await v.to_json() for k, v in self.commute_selectors.items()},
            "schools": {
                "primary": {
                    "school": await self.primary_school.to_json(),
                },
                "secondary": {
                    "school": await self.secondary_school.to_json(),
                },
            },
            "affordability": {
                "stamp_duty": await self.stamp_duty.to_json(),
                "council_tax": await self.council_tax.to_json(),
                "works_estimates": await self.works_estimates.to_json(),
                "total_works": await self.total_works.to_json(),
                "total_equity": await self.total_equity.to_json(),
                "life_insurance_total": await self.life_insurance_total.to_json(),
                "mortgage_required": await self.mortgage_required.to_json(),
                "monthly_mortgage": await self.monthly_mortgage.to_json(),
                "monthly_sinking_fund": await self.monthly_sinking_fund.to_json(),
                "monthly_commute_cost": await self.commute_breakdown.to_json(),
                "rental_income": await self.rental_income.to_json(),
                "group_monthly_cost": await self.group_monthly_cost.to_json(),
            },
            "annexe": {
                "payers": await self.annexe_payers.to_json_value(),
                "ignored": await self.annexe_ignored.to_json_value(),
            },
            "area": {
                "walkability": await self.walkability.to_json(),
                "town_description": await self.town_desc.to_json(),
            },
            "triage": {
                "favourite": await self.favourite.to_json(),
                "dismissed": await self.dismissed.to_json(),
                "is_viewed": await self.is_viewed.to_json(),
                "user_notes": await self.user_notes.to_json(),
                "triage_status": await self.triage_status.to_json(),
            },
            "comments": {
                "status": await self.comment_status.to_json(),
                "status_reason": await self.comment_status_reason.to_json(),
                "group_notes": await self.comment_group_notes.to_json(),
                "ashby_comments": await self.comment_ashby_comments.to_json(),
                "design_needed": await self.comment_design_needed.to_json(),
                "planning_needed": await self.comment_planning_needed.to_json(),
            },
            "settings": {
                "persons": await self._svc.persons_source.to_json(),
                "financial": {"status": "succeeded", "value": aggregate_dict(self._svc.setting_nodes)},
            },
        }
