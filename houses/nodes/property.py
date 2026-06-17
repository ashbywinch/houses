"""Property — the top-level container holding all DAG nodes for one property.

PropertyNodes creates and wires the SourceNodes and ComputedNodes for a single
property. When any source value changes, the Property emits its own ``changed``
signal so the API layer can push updates via WebSocket.
"""

from __future__ import annotations

from typing import Any

from dag.signals import Signal, Slot
from dag.source_node import SourceNode
from houses.geo import GeoPoint
from houses.nodes.location import BestAddressNode, BestLocationNode


class PropertyNodes:
    """Holds all DAG node references for one property.

    Creates SourceNodes for user-owned and enrichment data, and ComputedNodes
    for derived values (best_address, best_location). Signals are wired so
    that changes propagate reactively.
    """

    def __init__(self, rid: str) -> None:
        self.rid = rid
        self.changed = Signal()

        # Source nodes
        self.rightmove_url = SourceNode[str](f"{rid}/rightmove_url", str)
        self.rightmove_address = SourceNode[str](f"{rid}/rightmove_address", str)
        self.rightmove_bedrooms = SourceNode[str](f"{rid}/rightmove_bedrooms", str)
        self.rightmove_price = SourceNode[str](f"{rid}/rightmove_price", str)
        self.rightmove_location = SourceNode[GeoPoint](f"{rid}/rightmove_location", GeoPoint)
        self.precise_location = SourceNode[GeoPoint](f"{rid}/precise_location", GeoPoint)
        self.corrected_address = SourceNode[str](f"{rid}/corrected_address", str)

        # Computed nodes
        self.best_address = BestAddressNode(
            f"{rid}/best_address",
            corrected_address=self.corrected_address,
            rightmove_address=self.rightmove_address,
        )
        self.best_location = BestLocationNode(
            f"{rid}/best_location",
            precise_location=self.precise_location,
            rightmove_location=self.rightmove_location,
            best_address=self.best_address,
        )

        # Wire all source node signals → property changed
        all_nodes = [
            self.rightmove_url,
            self.rightmove_address,
            self.rightmove_bedrooms,
            self.rightmove_price,
            self.rightmove_location,
            self.precise_location,
            self.corrected_address,
        ]
        self._slots: list[Slot] = []
        for node in all_nodes:
            slot = Slot(self._on_node_changed)
            self._slots.append(slot)
            node.changed.connect(slot)

    def _on_node_changed(self) -> None:
        self.changed.emit()

    def to_json(self) -> dict[str, Any]:
        """Serialise the full property state."""
        return {
            "rid": self.rid,
            "best_address": self.best_address.to_json(),
            "best_location": self.best_location.to_json(),
        }
