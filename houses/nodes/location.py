from __future__ import annotations

from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from dag.user_input_node import UserInputNode
    from houses.nodes.geocode_node import GeocodeNode

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.signals import Slot
from houses.geopoint import GeoPoint
from houses.location import extract_postcode
from houses.model.geo import is_single_property_address


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


class BestLocationNode(DerivedNode[GeoPoint]):
    """Selects the best location from available sources.

    Priority: precise_location > geocode(best_address) > rightmove_location.

    All source nodes (precise, geocode, rightmove) are optional —
    if they have no value, fall through to the next source.
    Only best_address is a hard dependency.
    """

    def __init__(self, node_id: str, *, precise_location, rightmove_location, best_address, geocode=None):
        self._precise_location: UserInputNode[GeoPoint] = precise_location
        self._rightmove_location: UserInputNode[GeoPoint] = rightmove_location
        self._geocode: GeocodeNode | None = geocode
        super().__init__(node_id, GeoPoint, (best_address,))

        for src in (precise_location, rightmove_location, geocode):
            if src is None:
                continue
            slot = Slot(self._on_dep_changed)
            self._slots.append(slot)
            src.changed.connect(slot)

    @override
    def _is_stale(self) -> bool:
        if self._attempt.pending:
            return True
        if super()._is_stale():
            return True
        # Optional sources are detected the same way as BestAddressNode:
        # persisted after this node last computed.  No in-memory snapshots —
        # one is lost on restart and masks the first change after a source
        # that previously had no value.
        for src in (self._precise_location, self._rightmove_location, self._geocode):
            if src is None:
                continue
            if (
                src._persisted_at is not None
                and self._computed_at is not None
                and src._persisted_at > self._computed_at
            ):
                return True
        return False

    @override
    async def compute(self, address: Attempt[str]) -> Attempt[GeoPoint]:
        # Priority order: precise > geocode > rightmove. A missing geocode
        # node is skipped; a source with no value falls through.
        sources = (self._precise_location, self._geocode, self._rightmove_location)
        for src in (s for s in sources if s is not None):
            attempt = await src.attempt()
            if attempt.succeeded:
                return attempt

        if address.succeeded and is_single_property_address(address.value_or_none()):
            return self._impossible(
                {"best_address": address},
                extra=(f"address '{address.value_or_none()}' is single-property but all geocoding sources failed"),
            )
        return self._impossible({"best_address": address})
