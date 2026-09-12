from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import override

from money import Money

from dag.attempt import Attempt, Formula, FormulaLine, Provenance
from dag.derived_node import DerivedNode
from dag.node import Node

_FREQ_RE = re.compile(r"\d+x/wk · \d+ wks/yr")


def _patch_commute_frequency(prov: Provenance, live: dict[tuple[str, str], tuple[int, int]]) -> None:
    """Patch stale 'Nx/wk · M wks/yr' strings in per-commute subtrees.

    Each final_fuel subtree is keyed '<rid>/<person>/<label>/final_fuel'
    but the Provenance tree is keyed by node id without the rid — match
    on the trailing '<person>/<label>/...' path. The breakdown's own
    entries (from the live persons push) are the authority; the journey
    chain's Commute.destination stamp is route-plan-time history.
    Zero-trip destinations render WITHOUT a frequency suffix (matching
    Commute.to_provenance_value, which omits it when trips/weeks is 0).
    """

    def patch(node: Provenance, person: str | None, label: str | None) -> None:
        freq = live.get((person, label)) if person is not None and label is not None else None
        if freq is not None and isinstance(node.value, str) and "to " + (label or "") in node.value:
            trips, weeks = freq
            fresh = f"{trips}x/wk · {weeks} wks/yr" if trips and weeks else ""
            if _FREQ_RE.search(node.value):
                patched = _FREQ_RE.sub(fresh, node.value).replace(" ·  · ", " · ")
                node.value = patched[:-3] if patched.endswith(" · ") else patched
            elif fresh and node.value and not node.value.endswith(fresh):
                node.value = f"{node.value} · {fresh}"
        for key, child in (node.sources or {}).items():
            parts = key.split("/")
            if len(parts) >= 3 and parts[-1] in (
                "final_fuel",
                "merge",
                "commute",
                "computed_transit",
                "bus_augment",
                "park_and_ride",
                "drive",
                "walk",
                "tfl_no_bus",
                "tfl_with_bus",
                "rail_fare_if",
            ):
                patch(child, parts[-3], parts[-2])
            else:
                patch(child, person, label)

    for key, child in (prov.sources or {}).items():
        # Top-level keys are '<rid>/<person>/<label>/final_fuel' (plus the
        # persons source): the label is the segment before final_fuel.
        parts = key.split("/")
        if len(parts) >= 3 and parts[-1] == "final_fuel":
            patch(child, parts[-3], parts[-2])
        else:
            patch(child, None, None)

@dataclass(frozen=True)
class _CommuteEntryJson:
    """Wire shape of one per-person commute row (node_results)."""

    label: str
    trips_per_week: int
    weeks_per_year: int
    yearly_gbp: str

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
        return dict(
            label=self.label,
            trips_per_week=self.trips_per_week,
            weeks_per_year=self.weeks_per_year,
            yearly_gbp=self.yearly_gbp,
        )


@dataclass(frozen=True)
class _PersonCommuteJson:
    """Wire shape of one person's commute block (node_results)."""

    daily_gbp: str
    yearly_gbp: str
    commutes: list[_CommuteEntryJson]

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
        return dict(
            daily_gbp=self.daily_gbp,
            yearly_gbp=self.yearly_gbp,
            commutes=[c.to_dict() for c in self.commutes],
        )


@dataclass(frozen=True)
class _CommuteAggregateJson:
    """Wire shape of the CommuteBreakdownNode VALUE dict (node_results)."""

    persons: dict[str, dict]
    yearly_total_gbp: str
    formula_explanation: str

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
        return dict(
            persons=self.persons,
            yearly_total_gbp=self.yearly_total_gbp,
            formula_explanation=self.formula_explanation,
        )


class CommuteBreakdownNode(DerivedNode[dict]):
    """Aggregates commute costs across all persons and POIs."""

    
    def __init__(self, node_id: str, *, commute_selectors: Mapping[str, Node], persons_source: Node):
        # Live selectors dict — also captured by the deps closure below;
        # compute reads the attribute, the provider re-reads the dict on
        # every staleness/refresh check.  Mapping (read-only view) so
        # concrete node-typed dicts pass the type check.
        self._commute_selectors: Mapping[str, Node] = commute_selectors
        # Composition: the dep policy is a closure over the CONSTRUCTOR
        # ARGUMENTS — never over `self` state — so the base class can
        # evaluate it at any point in the node's life without touching
        # derived state. The dict is held by reference and mutated in
        # place by _on_persons_changed, so the dep set tracks the live
        # destination set (added/removed in Settings) with no rebuild
        # and no rewiring.
        super().__init__(
            node_id,
            dict,
            deps=lambda: (*commute_selectors.values(), persons_source),
        )

    @override
    @property
    def provenance_formula(self):

        v = self._attempt.value_or_none()
        if not self._attempt.succeeded or v is None:
            return None
        lines: list[FormulaLine] = []
        for name, pv in (v.get("persons") or {}).items():
            for c in pv.get("commutes") or ():
                yearly = Decimal(str(c.get("yearly_gbp") or 0))
                trips = c.get("trips_per_week", 0)
                weeks = c.get("weeks_per_year", 0)
                freq = f"{trips}x/wk · {weeks} wks/yr"
                lines.append(
                    FormulaLine(label=f"{name} → {c['label']} · {freq}", value=f"£{yearly:,.2f}/yr")
                )
        if not lines:
            return None
        return Formula(lines=lines, result=f"£{Decimal(str(v.get('yearly_total_gbp', '0'))):,.2f}/yr")

    @override
    def compute(self, *args: Attempt[dict]) -> Attempt[dict]:
        # Last arg is always persons_source, the rest are commute selectors
        if not args:
            return Attempt.succeeded(
                {
                    "persons": {},
                    "yearly_total_gbp": 0.0,
                    "formula_explanation": "No commute data",
                }
            )
        persons_attempt = args[-1]
        commute_attempts = args[:-1]

        persons_list = persons_attempt.value_or_none() if persons_attempt.succeeded else []
        yearly_total = Money(amount="0", currency="GBP")
        per_person: dict[str, dict] = {}
        selector_values = list(self._commute_selectors.values())
        for p in persons_list or []:
            person_yearly = Money(amount="0", currency="GBP")
            daily_amount: Money | None = None
            pois = p.get("places_of_interest", ()) if isinstance(p, dict) else getattr(p, "places_of_interest", ())
            name = p.get("name") if isinstance(p, dict) else getattr(p, "name", "?")
            if not isinstance(name, str):
                # A person entry without a usable name still needs a stable dict key.
                name = "?"
            commutes: list[_CommuteEntryJson] = []
            for poi in pois or ():
                key = f"{name}/{poi.label}"
                commute_node = self._commute_selectors.get(key)
                if commute_node is None:
                    continue
                idx = selector_values.index(commute_node) if commute_node in selector_values else -1
                attempt = (
                    commute_attempts[idx] if idx >= 0 and idx < len(commute_attempts) else commute_node.latest_attempt()
                )
                if not attempt.succeeded:
                    # A commute that cannot be computed propagates and this
                    # node never runs.  Reaching here means the selector map
                    # and the dependency set disagree — a defect.  Name it
                    # loudly: skipping the entry would publish a total that is
                    # quietly missing a person's cost (2026-09-10).
                    return Attempt.impossible(f"commute {key} has no computable result")
                val = attempt.value_or_none()
                if val is None:
                    return Attempt.impossible(f"commute {key} produced no value")
                daily = getattr(val, "daily_cost", None)
                if daily is not None:
                    daily_amount = daily
                    yearly_person_poi = daily_amount * poi.trips_per_week * poi.weeks_per_year
                    person_yearly += yearly_person_poi
                    yearly_total += yearly_person_poi
                    commutes.append(
                        _CommuteEntryJson(
                            label=poi.label,
                            trips_per_week=poi.trips_per_week,
                            weeks_per_year=poi.weeks_per_year,
                            yearly_gbp=str(yearly_person_poi.amount),
                        )
                    )
            per_person[name] = _PersonCommuteJson(
                daily_gbp=str(daily_amount.amount) if daily_amount is not None else "0",
                yearly_gbp=str(person_yearly.amount),
                commutes=commutes,
            ).to_dict()
        return Attempt.succeeded(
            _CommuteAggregateJson(
                persons=per_person,
                yearly_total_gbp=str(yearly_total.amount),
                formula_explanation="Aggregated from DAG nodes",
            ).to_dict()
        )

    @override
    async def build_provenance(self):
        """The aggregate as a human total, never the dict dump.

        The node VALUE stays the breakdown dict (the expression system
        reads yearly_total_gbp); only the provenance display value is
        swapped for the human figure.

        The per-commute subtrees are ALSO re-derived here: the persisted
        journey chain (selector → transit/walk/drive → merge → fuel)
        re-prices on live inputs but carries the trips/weeks STAMPED at
        route-plan time in Commute.destination. After a trips-only
        what-if (Pimlico 1→0 days, live 90970053) the chain's own rows
        still project 'to Pimlico · 1x/wk' while the aggregate prices
        £0 — the value is right and its provenance text is stale. The
        breakdown's own entries ARE the current trips (they come from
        the live persons push), so patch each final_fuel subtree's
        displayed Commute strings to the live frequency before
        persisting. Re-planning is untouched: only the displayed
        strings change, never the priced journeys.
        """
        prov = await super().build_provenance()
        v = self._attempt.value_or_none()
        if self._attempt.succeeded and isinstance(v, dict) and v.get("yearly_total_gbp") is not None:
            prov.value = f"£{Decimal(str(v['yearly_total_gbp'])):,.2f}/yr"
        live = {
            (name, c.get("label")): (c.get("trips_per_week", 0), c.get("weeks_per_year", 0))
            for name, pv in ((v.get("persons") if isinstance(v, dict) else None) or {}).items()
            for c in (pv.get("commutes") or ())
        }
        if live:
            _patch_commute_frequency(prov, live)
        return prov
