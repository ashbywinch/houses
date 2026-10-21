"""The property document the API reports: one node-map projection."""

from __future__ import annotations

from dag.node import NodeValue
from serialization import SerializableAsDict


class PropertyView(SerializableAsDict):
    """The property document the API reports: one node-map projection."""

    rid: str
    best_address: NodeValue
    best_location: NodeValue
    rightmove_url: NodeValue
    rightmove_price: NodeValue
    rightmove_bedrooms: NodeValue
    postcode: NodeValue
