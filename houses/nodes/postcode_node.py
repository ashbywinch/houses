"""The property's postcode — a projection of the best address."""

from __future__ import annotations

from typing import override

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from houses.location import extract_postcode


class PostcodeNode(DerivedNode[str]):
    """The property's postcode — a projection of the best address.

    Derived, never pushed: every address change (scrape, correction,
    manual details, address patch) recomputes it, so no write path can
    leave it blank.  Pending while the address is empty (a URL-only add
    has no postcode yet); "" when the address exists but has none.
    """

    def __init__(self, node_id: str, *, best_address):
        super().__init__(node_id, str, (best_address,))

    @override
    async def compute(self, address: Attempt[str]) -> Attempt[str]:
        if not address.succeeded:
            return Attempt.impossible(address.error or "no address")
        addr = address.value_or_none() or ""
        if not addr:
            return Attempt.pending()
        return Attempt.succeeded(extract_postcode(addr))
