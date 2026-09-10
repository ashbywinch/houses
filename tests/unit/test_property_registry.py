"""One live PropertyNodes per Rightmove id — a second one is a silent starver.

Two instances for one rid register the same node ids in the scheduler; the
second instance's derived nodes then queue behind the first instance's events
and never settle (the hazard `houses/server.py::_seed_dag` documents and works
around by reusing the registered instance).  Registration must not do it
silently — the failure is invisible at the call site and shows up much later as
figures that will not update.
"""

from __future__ import annotations

import pytest

from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import PropertyRegistry

RID = "12345678"


def test_registering_a_second_instance_for_a_property_fails_loudly():
    registry = PropertyRegistry()
    first = PropertyNodes(RID)
    registry.register(RID, first)

    registry.register(RID, first)  # the same instance: idempotent

    with pytest.raises(RuntimeError, match=RID):
        registry.register(RID, PropertyNodes(RID))

    assert registry.get(RID) is first, "the live instance must survive the refused call"


def test_removing_first_allows_a_fresh_instance():
    registry = PropertyRegistry()
    registry.register(RID, PropertyNodes(RID))

    registry.remove(RID)
    replacement = PropertyNodes(RID)
    registry.register(RID, replacement)

    assert registry.get(RID) is replacement
