"""Pick the property's best location from the available sources."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from dag.user_input_node import UserInputNode
    from houses.nodes.geocode_node import GeocodeNode

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.slot import Slot
from houses.geopoint import GeoPoint
from houses.model.geo import is_single_property_address


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
