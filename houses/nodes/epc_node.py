from __future__ import annotations

from dag.attempt import Attempt
from dag.computed_node import ComputedNode


class EpcNode(ComputedNode[dict]):
    def __init__(self, node_id: str, *, best_address):
        super().__init__(node_id, dict, (best_address,))

    async def compute(self, address: Attempt[str]) -> Attempt[dict]:
        if not address.succeeded:
            return self._impossible({"best_address": address})
        from houses.context import get_services

        addr = address.value_or_none() or ""
        svc = get_services()
        band = await svc.epc_service.lookup(addr)
        if band:
            return Attempt.succeeded({"band": band, "potential": band})
        return Attempt.impossible("no EPC data")

    async def build_provenance(self):
        from dag.attempt import Provenance
        return Provenance(label="EPC API")


class CouncilTaxNode(ComputedNode[dict]):
    def __init__(self, node_id: str, *, best_address, postcode_node):
        super().__init__(node_id, dict, (best_address, postcode_node))

    async def compute(self, address: Attempt[str],
                      postcode: Attempt[str]) -> Attempt[dict]:
        if not address.succeeded or not postcode.succeeded:
            extra = {}
            if not address.succeeded:
                extra["best_address"] = address
            if not postcode.succeeded:
                extra["postcode_node"] = postcode
            return self._impossible(extra)
        from houses.context import get_services

        addr = address.value_or_none() or ""
        svc = get_services()
        result = await svc.council_tax_service.lookup(postcode.value_or_none() or "", address=addr)
        if result.succeeded:
            val = result.value_or_none()
            return Attempt.succeeded({"band": val.band, "cost": val.yearly_cost})
        return Attempt.impossible("no council tax data")

    async def build_provenance(self):
        from dag.attempt import Provenance
        return Provenance(label="Council Tax")
