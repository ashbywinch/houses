from __future__ import annotations

from houses.nodes.property_nodes import PropertyNodes
from houses.services_provider import get_services


class PropertyRegistry:
    """The app-wide live registry of PropertyNodes keyed by Rightmove ID.

    Owned by the Services container (``Services.property_registry``) so it can
    be injected/replaced per context; production Services instances share the
    module default instance below so startup seeding and per-request reads see
    the same registry.
    """

    def __init__(self) -> None:
        self._properties: dict[str, PropertyNodes] = {}

    def register(self, rid: str, prop: PropertyNodes) -> None:
        self._properties[rid] = prop

    def get(self, rid: str) -> PropertyNodes | None:
        try:
            return self._properties[rid]
        except KeyError:
            return None

    def list_properties(self) -> list[str]:
        return list(self._properties)

    def remove(self, rid: str) -> None:
        """Drop a property from the registry (user-removed)."""
        self._properties.pop(rid, None)

    def clear(self) -> None:
        self._properties.clear()


DEFAULT_REGISTRY = PropertyRegistry()


def _active_registry() -> PropertyRegistry:
    """The registry to write/read: the active Services container's — or, when
    no container is bound (startup lifespan, ad-hoc scripts), a fresh container
    whose registry IS the shared default."""
    return get_services().property_registry


def register_property(rid: str, prop: PropertyNodes) -> None:
    _active_registry().register(rid, prop)


def get_property(rid: str) -> PropertyNodes | None:
    return _active_registry().get(rid)


def list_properties() -> list[str]:
    return _active_registry().list_properties()


def _reset() -> None:
    """Clear the active registry for test isolation."""
    _active_registry().clear()
