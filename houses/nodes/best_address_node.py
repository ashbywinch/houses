"""Pick the property's best address from the available sources."""

from __future__ import annotations

from typing import override

from dag.attempt import Attempt
from dag.derived_node import DerivedNode


class BestAddressNode(DerivedNode[str]):
    """Selects the best address from available sources.

    Priority: user_entered_address > corrected_address > rightmove_address.

    All three are static deps (any change re-schedules), but
    ``_get_active_deps`` includes only the sources carrying a value —
    a pending source (rightmove before the scrape lands, on a URL-only
    add) must not stall computing from the corrected/user-entered
    address.  Mirrors the park-and-ride conditional-dep pattern.
    """

    def __init__(self, node_id: str, *, user_entered_address, corrected_address, rightmove_address):
        super().__init__(
            node_id,
            str,
            (user_entered_address, corrected_address, rightmove_address),
            dep_names=("user_entered", "corrected", "rightmove"),
        )

    @override
    def _get_active_deps(self):
        """Only valued sources are active — a pending/empty source must
        not stall refresh.  The static deps still re-schedule the node
        when a source arrives later (e.g. the scrape's rightmove
        address landing after an address patch)."""
        active = []
        for src in self._deps:
            a = src.latest_attempt()
            if a.succeeded and a.value_or_none():
                active.append(src)
        return tuple(active)

    @override
    async def compute(
        self,
        user_entered: Attempt[str] | None = None,
        corrected: Attempt[str] | None = None,
        rightmove: Attempt[str] | None = None,
    ) -> Attempt[str]:
        for attempt in (user_entered, corrected, rightmove):
            if attempt is not None and attempt.succeeded:
                return attempt
        # No source has a value yet — stay pending until one arrives
        # (a fresh URL-only add has no address of any kind).
        return Attempt.pending()
