"""The acceptable school genders for the property's first child."""

from __future__ import annotations

from typing import Any, override

from dag.attempt import Attempt, SourceType, project_value
from dag.derived_node import DerivedNode
from dag.node import Node


class SchoolAcceptanceNode(DerivedNode[tuple[str, ...]]):
    """The acceptable school genders for the first child person — a
    PERSONS-SOURCED dep, never a constructor snapshot, so a filter edit
    (acceptable_schools change) recomputes the school lookup instead of
    freezing the acceptance set at property construction (Part F 2.2)."""

    provenance_source_type = SourceType.USER

    def __init__(self, node_id: str, *, persons_source: Node):
        super().__init__(node_id, tuple[str, ...], (persons_source,))
        self.display_name: str = "School acceptance"

    @override
    async def compute(self, persons: Attempt) -> Attempt[tuple[str, ...]]:
        val = persons.value_or_none() or []
        for p in val:
            if getattr(p, "is_child", False):
                return Attempt.succeeded(tuple(p.acceptable_schools))
        return Attempt.succeeded(("mixed",))

    @override
    def provenance_display_value(self, att) -> Any:
        # A raw string list reads as machine data in the tree;
        # "mixed, boys, girls" is the human form. The value stays a
        # tuple in the DAG — only its provenance rendering changes.
        if att.succeeded and isinstance(att.value, tuple):
            return ", ".join(project_value(v) for v in att.value)
        return att.value
