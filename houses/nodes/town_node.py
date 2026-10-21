"""The town name as the property's own address states it."""

from __future__ import annotations

from typing import override

from dag.attempt import Attempt, SourceType
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.walkability import extract_town


class TownNode(DerivedNode[str]):
    def __init__(self, node_id: str, *, best_address):
        deps: tuple[Node, ...] = (best_address,)
        super().__init__(node_id, str, deps)

    @override
    @staticmethod
    def compute(address: Attempt[str]) -> Attempt[str]:
        addr = address.value_or_none() or ""
        town = extract_town(addr)
        if town:
            return Attempt.succeeded(town)
        return Attempt.impossible("no town found in address")

    provenance_source_type = SourceType.CALC

    # Default build_provenance() walks active deps and uses provenance_source_type.
