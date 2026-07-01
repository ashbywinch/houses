from __future__ import annotations

import pytest

from dag.attempt import Provenance
from dag.source_node import SourceNode
from houses.geo import GeoPoint


class TestBestAddressNode:
    @pytest.fixture
    def nodes(self):
        user = SourceNode[str]("user", str)
        corrected = SourceNode[str]("corrected", str)
        rightmove = SourceNode[str]("rightmove", str)
        from houses.nodes.location import BestAddressNode
        node = BestAddressNode(
            "best_addr",
            user_entered_address=user,
            corrected_address=corrected,
            rightmove_address=rightmove,
        )
        return node, user, corrected, rightmove

    @pytest.mark.asyncio
    async def test_user_entered_takes_highest_priority(self, nodes):
        node, user, corrected, rightmove = nodes
        rightmove.push("RM Rd", Provenance("rightmove"))
        assert (await node.attempt()).value_or_none() == "RM Rd"
        corrected.push("User Rd", Provenance("user"))
        assert (await node.attempt()).value_or_none() == "User Rd"
        user.push("Best Rd", Provenance("user"))
        assert (await node.attempt()).value_or_none() == "Best Rd"

    @pytest.mark.asyncio
    async def test_corrected_takes_priority_over_rightmove(self, nodes):
        node, user, corrected, rightmove = nodes
        corrected.push("User Rd", Provenance("user"))
        rightmove.push("RM Rd", Provenance("rightmove"))
        a = await node.attempt()
        assert a.is_succeeded
        assert a.value_or_none() == "User Rd"

    @pytest.mark.asyncio
    async def test_fallback_to_rightmove(self, nodes):
        node, user, corrected, rightmove = nodes
        rightmove.push("RM Rd", Provenance("rightmove"))
        a = await node.attempt()
        assert a.is_succeeded
        assert a.value_or_none() == "RM Rd"

    @pytest.mark.asyncio
    async def test_all_impossible(self):
        from houses.nodes.location import BestAddressNode
        user = SourceNode[str]("u", str)
        corrected = SourceNode[str]("c", str)
        rightmove = SourceNode[str]("r", str)
        node = BestAddressNode("ba", user_entered_address=user,
                                corrected_address=corrected,
                                rightmove_address=rightmove)
        a = await node.attempt()
        assert not a.is_succeeded
        assert "user_entered_address" in a._error
        assert "corrected_address" in a._error
        assert "rightmove_address" in a._error

    @pytest.mark.asyncio
    async def test_to_json_shape(self, nodes):
        node, user, corrected, rightmove = nodes
        corrected.push("10 High St", Provenance("user"))
        j = await node.to_json()
        assert j["succeeded"] is True
        assert j["value"] == "10 High St"
        assert j["error"] is None


class TestBestLocationNode:
    @pytest.mark.asyncio
    async def test_precise_takes_priority(self):
        from houses.nodes.location import BestLocationNode

        precise = SourceNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = SourceNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = SourceNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        gp = GeoPoint(51.5, -0.1)
        precise.push(gp, Provenance("user"))

        a = await node.attempt()
        assert a.is_succeeded
        assert a.value_or_none() == gp

    @pytest.mark.asyncio
    async def test_rightmove_used_when_precise_missing_and_vague_address(self):
        from houses.nodes.location import BestLocationNode

        precise = SourceNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = SourceNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = SourceNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        rm_gp = GeoPoint(51.4, -0.2)
        rightmove_loc.push(rm_gp, Provenance("rightmove"))
        best_addr.push("London", Provenance("rightmove"))

        a = await node.attempt()
        assert a.is_succeeded
        assert a.value_or_none() == rm_gp

    @pytest.mark.asyncio
    async def test_impossible_when_no_sources(self):
        from houses.nodes.location import BestLocationNode

        precise = SourceNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = SourceNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = SourceNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        a = await node.attempt()
        assert not a.is_succeeded
        assert "best_loc" in a._error

    @pytest.mark.asyncio
    async def test_impossible_mentions_all_failed_deps(self):
        from houses.nodes.location import BestLocationNode

        precise = SourceNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = SourceNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = SourceNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        a = await node.attempt()
        assert "precise_location" in a._error
        assert "rightmove_location" in a._error
        assert "best_address" in a._error

    @pytest.mark.asyncio
    async def test_recomputes_when_precise_updated(self):
        from houses.nodes.location import BestLocationNode

        precise = SourceNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = SourceNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = SourceNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        rm_gp = GeoPoint(51.4, -0.2)
        rightmove_loc.push(rm_gp, Provenance("rightmove"))
        assert (await node.attempt()).value_or_none() == rm_gp

        user_gp = GeoPoint(51.5, -0.1)
        precise.push(user_gp, Provenance("user"))
        assert (await node.attempt()).value_or_none() == user_gp

    @pytest.mark.asyncio
    async def test_to_json_with_succeeded(self):
        from houses.nodes.location import BestLocationNode

        precise = SourceNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = SourceNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = SourceNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        gp = GeoPoint(51.5, -0.1)
        precise.push(gp, Provenance("user"))
        j = await node.to_json()
        assert j["succeeded"] is True
        assert j["value"] == {"lat": 51.5, "lon": -0.1}

    @pytest.mark.asyncio
    async def test_to_json_with_impossible(self):
        from houses.nodes.location import BestLocationNode

        precise = SourceNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = SourceNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = SourceNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        j = await node.to_json()
        assert j["succeeded"] is False
        assert j["value"] is None
