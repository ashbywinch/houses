from __future__ import annotations

from dataclasses import replace

from money import Money

from dag.attempt import Attempt, Provenance, SourceType
from dag.derived_node import DerivedNode
from dag.measurement import Measurement
from houses.council_tax_info import CouncilTaxInfo
from houses.services_provider import get_services

# Fallback when the council tax lookup fails: a Band D estimate with a
# spread, so the total can show "≈" instead of a bare "?" (Part A).
_FALLBACK_YEARLY_COST = Money("1200", "GBP")
_FALLBACK_STDDEV = 50.0


class EpcNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, best_address, postcode_node):
        super().__init__(node_id, dict, (best_address, postcode_node))

    async def compute(self, address: Attempt[str], postcode: Attempt[str]) -> Attempt[dict]:
        if not address.succeeded:
            return self._impossible({"best_address": address})
        addr = address.value_or_none() or ""
        postcode_val = postcode.value_or_none() or ""
        svc = get_services()
        result = await svc.epc_service.lookup(postcode_val, address=addr)
        if result.succeeded:
            band = result.value_or_none()
            if band:
                return Attempt.succeeded({"band": band, "potential": band})
            return Attempt.impossible("no EPC data")
        # Propagate the real reason (e.g. ambiguous address) so the frontend
        # can show it — not a generic "no EPC data".
        return Attempt.impossible(result.error or "no EPC data")

    @property
    def provenance_source_type(self) -> SourceType:
        return SourceType.API


class CouncilTaxNode(DerivedNode[CouncilTaxInfo]):
    def __init__(self, node_id: str, *, best_address, postcode_node):
        super().__init__(node_id, CouncilTaxInfo, (best_address, postcode_node))

    async def compute(self, address: Attempt[str], postcode: Attempt[str]) -> Attempt[CouncilTaxInfo]:
        if not address.succeeded or not postcode.succeeded:
            extra = {}
            if not address.succeeded:
                extra["best_address"] = address
            if not postcode.succeeded:
                extra["postcode_node"] = postcode
            return self._impossible(extra)
        addr = address.value_or_none() or ""
        svc = get_services()
        result = await svc.council_tax_service.lookup(postcode.value_or_none() or "", address=addr)
        if result.succeeded:
            info = result.value_or_none()
            if info is None:
                return Attempt.impossible("no council tax data")
            # Normalise: the value always carries a Measurement, exact
            # (stddev 0) when the lookup succeeded.
            if info.yearly_cost is not None and not isinstance(info.yearly_cost, Measurement):
                info = replace(info, yearly_cost=Measurement(info.yearly_cost, 0.0))
            return Attempt.succeeded(info)
        # Lookup failed — return a Band D estimate with a spread instead
        # of plain "?"; provenance notes the fallback (Part A).
        return Attempt.succeeded(
            CouncilTaxInfo(
                band="?",
                yearly_cost=Measurement(_FALLBACK_YEARLY_COST, _FALLBACK_STDDEV),
            )
        )

    @property
    def provenance_source_type(self) -> SourceType:
        return SourceType.API

    async def build_provenance(self) -> Provenance:
        p = await super().build_provenance()
        v = self._attempt.value_or_none()
        if (
            self._attempt.succeeded
            and v is not None
            and v.yearly_cost is not None
            and v.yearly_cost.stddev > 0
        ):
            p.description = "Council tax estimated — address lookup failed."
        if self._attempt.succeeded and v is not None and v.evidence_url:
            p.url = v.evidence_url
        return p

    # Default build_provenance() walks best_address and postcode deps.
