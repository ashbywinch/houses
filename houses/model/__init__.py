from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class NodeKind(enum.Enum):
    source = "source"
    user_input = "user_input"
    derived = "derived"


@dataclass
class NodeDef:
    id: str
    kind: NodeKind
    deps: list[str] = field(default_factory=list)
    compute: Callable[..., Any] | None = None
    provenance_template: str = ""
    user_table: str | None = None

    def __post_init__(self):
        if self.kind == NodeKind.derived and self.compute is None:
            raise ValueError(f"derived node {self.id} must have a compute function")
        if self.kind == NodeKind.user_input and self.user_table is None:
            raise ValueError(f"user_input node {self.id} must have a user_table")


@dataclass
class NodeResult:
    node_id: str
    value: Any
    source: str
    row_id: int | None = None


@dataclass
class SourceRow:
    row_id: int
    value: Any
    source: str
    created_at: datetime


@dataclass
class UserRow:
    row_id: int
    value: Any
    created_at: datetime


@dataclass
class DerivedRow:
    value: Any
    dep_versions: dict[str, int | None]
    source: str
    error: str | None
    updated_at: datetime


@dataclass
class PropertyData:
    rid: str
    sources: dict[str, SourceRow] = field(default_factory=dict)
    user_inputs: dict[str, UserRow] = field(default_factory=dict)
    derived: dict[str, DerivedRow] = field(default_factory=dict)
