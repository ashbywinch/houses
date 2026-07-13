from __future__ import annotations

from houses.nodes.property import PropertyNodes

_registry: dict[str, PropertyNodes] = {}


def register_property(rid: str, prop: PropertyNodes) -> None:
    _registry[rid] = prop
