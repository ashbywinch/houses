from __future__ import annotations

from dag.attempt import Attempt, Provenance, SourceType
from dag.derived_node import DerivedNode
from houses.council_tax_info import CouncilTaxInfo
from houses.services_provider import get_services


class EpcNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, best_address, postcode_node):
        super().__init__(node_id, dict, (best_address, postcode_node))

    async def compute(self, address: Attempt[str], postcode: Attempt[str]) -> Attempt[dict]:
        if not address.succeeded:
            return self._impossible({"best_address": address})
        addr = address.value_or_none() or ""
        postcode_val = postcode.value_or_none() or ""
        svc = get_services()
        band = await svc.epc_service.lookup(postcode_val, address=addr)
        if band:
            return Attempt.succeeded({"band": band, "potential": band})
        return Attempt.impossible("no EPC data")

    @property
    def provenance_source_type(self) -> SourceType:
        return SourceType.API

    async def build_provenance(self):
        return Provenance(
            label="EPC API",
            url="https://www.epcregister.com/",
            source_type=SourceType.API,
            freshness=self._attempt.created_at,
        )


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
            return Attempt.succeeded(result.value_or_none())
        return Attempt.impossible("no council tax data")

    @property
    def provenance_source_type(self) -> SourceType:
        return SourceType.API

    async def build_provenance(self):
        return Provenance(
            label="Council Tax",
            url="https://www.gov.uk/council-tax-bands",
            source_type=SourceType.API,
            freshness=self._attempt.created_at,
        )
