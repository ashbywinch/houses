from __future__ import annotations

from houses.nodes.property import PropertyNodes

_registry: dict[str, PropertyNodes] = {}


def _reset():
    """Clear the property registry for test isolation."""
    _registry.clear()


def register_property(rid: str, prop: PropertyNodes) -> None:
    _registry[rid] = prop


def get_property(rid: str) -> PropertyNodes | None:
    return _registry.get(rid)


def list_properties() -> list[str]:
    return list(_registry.keys())
