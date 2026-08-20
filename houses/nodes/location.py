from __future__ import annotations

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from houses.geo import GeoPoint
from houses.model.geo import is_single_property_address


class BestAddressNode(DerivedNode[str]):
    """Selects the best address from available sources.

    Priority: user_entered_address > corrected_address > rightmove_address.

    user_entered_address and corrected_address are optional — only
    rightmove_address is a hard dependency (every property with a
    sheet address pushes to it).
    """

    def __init__(self, node_id: str, *, user_entered_address, corrected_address, rightmove_address):
        self._user_entered = user_entered_address
        self._corrected = corrected_address
        super().__init__(node_id, str, (rightmove_address,))

        from dag.signals import Slot

        # Optional sources are NOT hard deps (a permanently pending input
        # must not block refresh), but their changes must still schedule a
        # recompute — mirror BestLocationNode's optional-dep wiring.
        for src in (self._user_entered, self._corrected):
            slot = Slot(self._on_dep_changed)
            self._slots.append(slot)
            src.changed.connect(slot)

    def _is_stale(self) -> bool:
        if self._attempt.pending:
            return True
        if super()._is_stale():
            return True
        # Detect optional-source changes exactly like the base class does
        # for hard deps: a source persisted after this node last computed
        # means the node's value predates it.  No in-memory snapshot — a
        # snapshot is lost on restart and ambiguous when the source never
        # had a value, which would mask the first push.
        for src in (self._user_entered, self._corrected):
            if (
                src._persisted_at is not None
                and self._computed_at is not None
                and src._persisted_at > self._computed_at
            ):
                return True
        return False

    async def compute(self, rightmove: Attempt[str]) -> Attempt[str]:
        # Check optional sources in priority order
        user_attempt = await self._user_entered.attempt()
        if user_attempt.succeeded:
            return user_attempt
        corrected_attempt = await self._corrected.attempt()
        if corrected_attempt.succeeded:
            return corrected_attempt
        if rightmove.succeeded:
            return rightmove
        return self._impossible({"rightmove_address": rightmove})


class BestLocationNode(DerivedNode[GeoPoint]):
    """Selects the best location from available sources.

    Priority: precise_location > geocode(best_address) > rightmove_location.

    All source nodes (precise, geocode, rightmove) are optional —
    if they have no value, fall through to the next source.
    Only best_address is a hard dependency.
    """

    def __init__(self, node_id: str, *, precise_location, rightmove_location, best_address, geocode=None):
        self._precise_location = precise_location
        self._rightmove_location = rightmove_location
        self._geocode = geocode
        super().__init__(node_id, GeoPoint, (best_address,))

        from dag.signals import Slot

        for src in (precise_location, rightmove_location):
            slot = Slot(self._on_dep_changed)
            self._slots.append(slot)
            src.changed.connect(slot)
        if geocode is not None:
            slot = Slot(self._on_dep_changed)
            self._slots.append(slot)
            geocode.changed.connect(slot)

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

    async def compute(self, address: Attempt[str]) -> Attempt[GeoPoint]:

        # Check precise_location first (optional — may be pending)
        precise_attempt = await self._precise_location.attempt()
        if precise_attempt.succeeded:
            return precise_attempt

        # Check geocode (optional — may be pending)
        if self._geocode is not None:
            geocode_attempt = await self._geocode.attempt()
            if geocode_attempt.succeeded:
                return geocode_attempt

        # Check rightmove_location (optional — may be pending)
        rightmove_attempt = await self._rightmove_location.attempt()
        if rightmove_attempt.succeeded:
            return rightmove_attempt

        if address.succeeded and is_single_property_address(address.value_or_none()):
            return self._impossible(
                {"best_address": address},
                extra=(f"address '{address.value_or_none()}' is single-property but all geocoding sources failed"),
            )
        return self._impossible({"best_address": address})
