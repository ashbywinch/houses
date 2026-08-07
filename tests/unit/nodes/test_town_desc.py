"""Tests for TownDescNode — the town description must not stall on a
pending postcode (same bug family as park_and_ride): a property with no
postcode still gets its town summary, using an empty postcode string.
"""

from __future__ import annotations

import pytest

from dag.attempt import Attempt
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint
from houses.nodes.area import TownDescNode


class _FakeTownDesc:
    """TownDescService fake — records the town + postcode it was given."""

    def __init__(self, text: str = "A leafy suburb."):
        self.text = text
        self.calls: list[tuple[str, str]] = []

    async def describe(self, town_name: str, postcode: str) -> Attempt[str]:
        self.calls.append((town_name, postcode))
        return Attempt.succeeded(self.text)


def _node(node_id: str, *, postcode_node):
    loc = UserInputNode[GeoPoint](f"{node_id}_loc", GeoPoint)
    nearest = UserInputNode[str](f"{node_id}_nearest", str)
    town = UserInputNode[str](f"{node_id}_town", str)
    node = TownDescNode(
        node_id,
        best_location=loc,
        nearest_town=nearest,
        town_name=town,
        postcode_node=postcode_node,
    )
    return node, loc, nearest, town


class TestTownDescNode:
    @pytest.mark.asyncio
    async def test_pending_postcode_does_not_stall(self):
        """Regression: a pending postcode (no producer) must NOT left the
        town description pending/unrecoverable — it computes with an
        empty postcode string and the Summary section renders."""
        from houses.services_provider import _request_services
        from tests.helpers import make_services

        fake = _FakeTownDesc("A quiet village.")
        svc = make_services(town_desc_service=fake)
        token = _request_services.set(svc)
        try:
            postcode = UserInputNode[str]("td1_pc", str)  # never pushed → pending
            node, loc, nearest, town = _node("td1", postcode_node=postcode)
            loc.push(GeoPoint(51.5, -0.1), "test")
            nearest.push("Maidenhead", "test")
            town.push("Maidenhead", "test")
            await flush_processor()
            a = await node.attempt()
            assert a.succeeded, f"town desc must compute without a postcode, got {a.status}: {a.error}"
            assert a.value_or_none() == {"description": "A quiet village."}
            assert fake.calls == [("Maidenhead", "")], "postcode should be empty, not missing"
        finally:
            _request_services.reset(token)

    @pytest.mark.asyncio
    async def test_postcode_used_when_available(self):
        """When the postcode IS known it is passed to the describe call."""
        from houses.services_provider import _request_services
        from tests.helpers import make_services

        fake = _FakeTownDesc("A leafy suburb.")
        svc = make_services(town_desc_service=fake)
        token = _request_services.set(svc)
        try:
            postcode = UserInputNode[str]("td2_pc", str)
            postcode.push("SL6 3YZ", "test")
            node, loc, nearest, town = _node("td2", postcode_node=postcode)
            loc.push(GeoPoint(51.5, -0.1), "test")
            nearest.push("Maidenhead", "test")
            town.push("Maidenhead", "test")
            await flush_processor()
            a = await node.attempt()
            assert a.succeeded
            assert fake.calls == [("Maidenhead", "SL6 3YZ")]
        finally:
            _request_services.reset(token)
