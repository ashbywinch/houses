from __future__ import annotations

import logging

from dag.attempt import Attempt, Provenance
from dag.computed_node import ComputedNode

logger = logging.getLogger(__name__)


class EpcNode(ComputedNode[str]):
    """Async node that looks up EPC rating via the EPC service.

    Deps: (best_address)
    """

    def __init__(self, node_id: str, *, best_address):
        super().__init__(
            node_id,
            str,
            (best_address,),
        )

    async def compute(self, best_address: Attempt[str]) -> Attempt[str]:
        from houses.context import get_services

        if not best_address.is_succeeded:
            return self._impossible({"best_address": best_address})
        address = best_address.value_or_none()
        rating = await get_services().epc_service.lookup(address, address)
        if rating:
            return Attempt.succeeded(
                rating,
                Provenance("EPC API", description=f"EPC rating for {address}"),
            )
        return Attempt.impossible(f"no EPC rating found for {address}",
                                   Provenance("EPC API", description=f"lookup for {address}"))


class CouncilTaxNode(ComputedNode[dict]):
    """Async node that looks up council tax via the council tax service.

    Deps: (best_address, postcode_node)
    Uses the postcode for the API lookup; the full address is for context.
    """

    def __init__(self, node_id: str, *, best_address, postcode_node):
        super().__init__(
            node_id,
            dict,
            (best_address, postcode_node),
        )

    async def compute(self, best_address: Attempt[str],
                      postcode_attempt: Attempt[str]) -> Attempt[dict]:
        from houses.context import get_services

        if not postcode_attempt.is_succeeded:
            return self._impossible({"postcode": postcode_attempt})
        postcode = postcode_attempt.value_or_none()
        address = best_address.value_or_none() if best_address.is_succeeded else postcode
        result = await get_services().council_tax_service.lookup(postcode, address)
        if result.is_succeeded:
            ct = result.value_or_none()
            return Attempt.succeeded(
                {"band": ct.band, "yearly_cost": ct.yearly_cost},
                Provenance("Council Tax", description=f"council tax for {postcode}"),
            )
        error_detail = getattr(result, '_reason', None) or getattr(result, '_error', None) or 'unknown'
        return Attempt.impossible(f"council_tax_lookup: {error_detail}",
                                   Provenance("Council Tax", description=f"lookup for {address}"))
