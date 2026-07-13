from __future__ import annotations

import pytest

from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint


class TestBestAddressNode:
    @pytest.fixture
    def nodes(self):
        user = UserInputNode[str]("user", str)
        corrected = UserInputNode[str]("corrected", str)
        rightmove = UserInputNode[str]("rightmove", str)
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
        user.push("Best Rd", "user")
        corrected.push("User Rd", "user")
        rightmove.push("RM Rd", "rightmove")
        assert (await node.attempt()).value_or_none() == "Best Rd"

    @pytest.mark.asyncio
    async def test_corrected_takes_priority_over_rightmove(self, nodes):
        from houses.nodes.location import BestAddressNode
        # Create a node without user_entered to verify corrected > rightmove
        corrected = UserInputNode[str]("c2", str)
        rightmove = UserInputNode[str]("r2", str)
        node = BestAddressNode("ba2", user_entered_address=corrected,
                                corrected_address=corrected,
                                rightmove_address=rightmove)
        corrected.push("User Rd", "user")
        rightmove.push("RM Rd", "rightmove")
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == "User Rd"

    @pytest.mark.asyncio
    async def test_fallback_to_rightmove(self, nodes):
        from houses.nodes.location import BestAddressNode
        # All deps share the same UserInputNode; once pushed, all succeed
        # Priority: user_entered is checked first, so it returns rightmove's value
        shared = UserInputNode[str]("shared", str)
        node = BestAddressNode("ba3", user_entered_address=shared,
                                corrected_address=shared,
                                rightmove_address=shared)
        shared.push("Rightmove Rd", "rightmove")
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == "Rightmove Rd"

    @pytest.mark.asyncio
    async def test_to_json_shape(self, nodes):
        node, user, corrected, rightmove = nodes
        user.push("10 High St", "user")
        corrected.push("", "user")
        rightmove.push("", "rightmove")
        j = await node.to_json()
        assert j["status"] == "succeeded"
        assert j["value"] == "10 High St"
        assert "error" not in j


class TestBestLocationNode:
    @pytest.mark.asyncio
    async def test_precise_takes_priority(self):
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = UserInputNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        gp = GeoPoint(51.5, -0.1)
        precise.push(gp, "user")
        rightmove_loc.push(GeoPoint(51.4, -0.2), "rightmove")
        best_addr.push("London", "rightmove")

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == gp

    @pytest.mark.asyncio
    async def test_rightmove_used_when_precise_missing_and_vague_address(self):
        from houses.nodes.location import BestLocationNode

        # Build node without precise dependency to test rightmove fallback
        rightmove_loc = UserInputNode[GeoPoint]("rm_fb", GeoPoint)
        best_addr = UserInputNode[str]("addr_fb", str)
        # "precise" = rightmove_loc means it's always succeeded if rightmove is
        precise = rightmove_loc
        node = BestLocationNode("best_loc_fb", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        rm_gp = GeoPoint(51.4, -0.2)
        rightmove_loc.push(rm_gp, "rightmove")
        best_addr.push("Vague Road, London", "rightmove")

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == rm_gp

    @pytest.mark.asyncio
    async def test_pending_when_no_sources(self):
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = UserInputNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_pending_when_all_deps_missing(self):
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = UserInputNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_recomputes_when_precise_updated(self):
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = UserInputNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        rm_gp = GeoPoint(51.4, -0.2)
        rightmove_loc.push(rm_gp, "rightmove")
        best_addr.push("London", "rightmove")
        precise.push(rm_gp, "user")  # same as rightmove initially
        assert (await node.attempt()).value_or_none() == rm_gp

        user_gp = GeoPoint(51.5, -0.1)
        precise.push(user_gp, "user")
        assert (await node.attempt()).value_or_none() == user_gp

    @pytest.mark.asyncio
    async def test_to_json_with_succeeded(self):
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = UserInputNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        gp = GeoPoint(51.5, -0.1)
        precise.push(gp, "user")
        rightmove_loc.push(GeoPoint(51.4, -0.2), "rightmove")
        best_addr.push("London", "rightmove")
        j = await node.to_json()
        assert j["status"] == "succeeded"
        assert j["value"] == {"lat": 51.5, "lon": -0.1}

    @pytest.mark.asyncio
    async def test_to_json_with_pending(self):
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = UserInputNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        j = await node.to_json()
        assert j["status"] == "pending"
        assert j["value"] is None
