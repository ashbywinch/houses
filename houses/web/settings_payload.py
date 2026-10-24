"""The settings document, shared by the GET endpoint and the websocket push.

One shape for both surfaces: persons (enriched for the session), financial
aggregates, commute thresholds, the household deposit as ONE server-computed
number, and the what-if flag.  Lives in its own module so the broadcaster can
push it without importing the router — a top-level import from
``houses.web.api_router`` would be the router's own import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as _Decimal
from typing import Any

from money import Money
from pydantic import Field

from houses.model.domain import (
    EquityInputs,
    Person,
    effective_acceptable_modes,
    effective_editable_by,
    effective_selling_home,
    equity_line,
    home_equity_contributions,
    person_id_of,
)
from houses.nodes.settings_node import aggregate_dict
from houses.services_provider import get_services
from houses.web.auth import SessionClaims
from serialization import MoneyValue, SerializableAsDict


class ProvenanceLine(SerializableAsDict):
    """One deposit provenance line."""

    label: str
    value: str


class DepositFormula(SerializableAsDict):
    """The deposit provenance formula block."""

    lines: list[ProvenanceLine]
    result: str


class DepositProvenance(SerializableAsDict):
    """The household-deposit provenance block."""

    label: str
    value: str
    source_type: str = Field(alias="sourceType")
    formula: DepositFormula


class HouseholdDeposit(SerializableAsDict):
    """One server-computed total, per-person amounts, and provenance lines."""

    total: MoneyValue
    persons: dict[str, MoneyValue]
    provenance: DepositProvenance


class FinancialBlock(SerializableAsDict):
    """The financial block: status plus the aggregate value."""

    status: str
    value: Any


class SettingsPayload(SerializableAsDict):
    """The settings document (GET /api/settings and the websocket push)."""

    persons: dict[str, Any]
    financial: FinancialBlock
    commute_thresholds: dict[str, Any]
    household_deposit: HouseholdDeposit
    what_if_active: bool



def _registry_property(rid: str):
    """The live PropertyNodes for *rid* from the request-scoped registry."""
    return get_services().property_registry.get(rid)


@dataclass(frozen=True)
class DepositBreakdown:
    """(persons, total, lines) from _deposit_breakdown — named so callers
    read the fields by meaning, not position."""

    persons: dict
    total: Money
    lines: list[ProvenanceLine]


def _home_property_address(person) -> str:
    """First street line of the linked house's best address; '' when unset."""
    linked_rid = getattr(person, "home_property_rid", "")
    if not linked_rid:
        return ""
    prop = _registry_property(linked_rid)
    if prop is None:
        return ""
    att = prop.best_address.latest_attempt()
    if not (att.succeeded and att.value_or_none()):
        return ""
    return str(att.value_or_none()).split("\n")[0].split(",")[0]


@dataclass(frozen=True)
class SessionPersons:
    """The person list plus the requesting session's user — the pair the
    settings endpoints share for session-aware ownership decisions."""

    persons: list
    session_user: SessionClaims | None

    def session_name(self) -> str:
        """The session user's Person name (email match), or "" when unlinked."""
        if not self.session_user:
            return ""
        folded = self.session_user.email.casefold()
        for p in self.persons:
            email = getattr(p, "email", "")
            if email and email.casefold() == folded:
                return getattr(p, "name", "")
        return ""

    def can_edit(self, session_name: str, person) -> bool:
        """Server-side ownership check — the UI never decides this."""
        if not self.session_user:
            return False
        if self.session_user.is_superuser:
            return True
        if not session_name:
            return False
        return session_name == person.name or session_name in effective_editable_by(person, self.persons)


def _enrich_persons(dumped: object, view: SessionPersons, session_name: str) -> None:
    """Enrich serialized persons with the EFFECTIVE per-POI modes, the
    effective guardian list, the session-aware editable_by_me flag, and
    the linked-house address.  The server decides ownership; the UI only
    renders it.  Entries are matched to Person models BY NAME — a legacy
    non-Person entry in the source must not crash the enrichment."""
    if not isinstance(dumped, list):
        return
    by_name = {p.name: p for p in view.persons}
    for item in dumped:
        if not isinstance(item, dict):
            continue
        person = by_name.get(item.get("name") or "")
        if person is None:
            continue
        editable_by = effective_editable_by(person, view.persons)
        item["editable_by"] = list(editable_by)
        item["editable_by_me"] = view.can_edit(session_name, person)
        item["selling_home"] = effective_selling_home(person)
        # lucidlint: ignore loop-pipeline the loop ENRICHES each serialized POI in place — no collection is built
        for poi_item, poi in zip(item.get("places_of_interest") or (), person.places_of_interest, strict=False):
            if isinstance(poi_item, dict):
                poi_item["acceptable_modes"] = list(effective_acceptable_modes(poi))
        addr = _home_property_address(person)
        if addr:
            item["home_property_address"] = addr


async def settings_payload(session_user: SessionClaims | None = None) -> SettingsPayload:
    """The settings document: persons, financial aggregates, commute
    thresholds, the household deposit, and the what-if flag. Shared by
    the GET endpoint and the settings_updated websocket push, so both
    surfaces always speak the same shape. Returns the model; each caller
    dumps it at its own serialization edge (FastAPI, json.dumps)."""
    svc = get_services()
    persons_json = await svc.persons_source.to_json()
    attempt = svc.persons_source.latest_attempt()
    persons = [p for p in (attempt.value_or_none() or []) if isinstance(p, Person)]
    view = SessionPersons(persons=persons, session_user=session_user)
    session_name = view.session_name()
    _enrich_persons(persons_json.get("value"), view, session_name)

    # The family deposit as ONE number (P4): per person, sale proceeds −
    # remaining mortgage + extra money, plus the household total —
    # computed server-side, never derived from parts by the client.
    breakdown = _deposit_breakdown(persons)
    deposit_persons, deposit_total, deposit_lines = breakdown.persons, breakdown.total, breakdown.lines
    started = (svc.whatif_started_at.latest_attempt().value_or_none() or "").strip()

    commute_thresholds = await svc.commute_thresholds_source.to_json()
    return SettingsPayload(
        persons=persons_json,
        financial=FinancialBlock(status="succeeded", value=aggregate_dict(svc.setting_nodes)),
        commute_thresholds=commute_thresholds,
        household_deposit=HouseholdDeposit(
            total=deposit_total,
            persons=deposit_persons,
            provenance=DepositProvenance(
                label="Household Deposit",
                value=f"£{deposit_total.amount:,.2f}",
                source_type="calc",
                formula=DepositFormula(lines=deposit_lines, result=f"£{deposit_total.amount:,.2f}"),
            ),
        ),
        what_if_active=bool(started),
    )


def _deposit_breakdown(persons: list) -> DepositBreakdown:
    """Per-person deposit (distributed home equity + cash) and the
    household total. Home equity splits by co-owner shares; children
    never contribute. Pure — unit-testable without the request (P4)."""
    contributions = home_equity_contributions(persons)
    by_name = {p.name: p for p in persons if not p.is_child}
    deposit_persons: dict[str, Money] = {}
    deposit_total = Money(amount="0", currency="GBP")
    deposit_lines: list[ProvenanceLine] = []
    for name, person in by_name.items():
        cash = person.cash_contribution.amount
        home_share = contributions.get(person_id_of(person), _Decimal("0"))
        value = home_share + cash
        deposit_persons[name] = Money(str(value), "GBP")
        deposit_total = deposit_total + Money(str(value), "GBP")
        deposit_lines.append(
            ProvenanceLine(
                label=name,
                value=equity_line(EquityInputs(name, person, contributions, persons, cash=cash, show_cash=True)),
            )
        )
    return DepositBreakdown(deposit_persons, deposit_total, deposit_lines)
