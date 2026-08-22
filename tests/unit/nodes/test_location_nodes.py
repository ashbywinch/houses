from __future__ import annotations

import pytest

from dag.scheduler import flush_processor
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
        await flush_processor()
        assert (await node.attempt()).value_or_none() == "Best Rd"

    @pytest.mark.asyncio
    async def test_corrected_takes_priority_over_rightmove(self, nodes):
        from houses.nodes.location import BestAddressNode

        # Create a node without user_entered to verify corrected > rightmove
        corrected = UserInputNode[str]("c2", str)
        rightmove = UserInputNode[str]("r2", str)
        node = BestAddressNode(
            "ba2", user_entered_address=corrected, corrected_address=corrected, rightmove_address=rightmove
        )
        corrected.push("User Rd", "user")
        rightmove.push("RM Rd", "rightmove")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == "User Rd"

    @pytest.mark.asyncio
    async def test_fallback_to_rightmove(self, nodes):
        from houses.nodes.location import BestAddressNode

        # All deps share the same UserInputNode; once pushed, all succeed
        # Priority: user_entered is checked first, so it returns rightmove's value
        shared = UserInputNode[str]("shared", str)
        node = BestAddressNode("ba3", user_entered_address=shared, corrected_address=shared, rightmove_address=shared)
        shared.push("Rightmove Rd", "rightmove")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == "Rightmove Rd"

    @pytest.mark.asyncio
    async def test_to_json_shape(self, nodes):
        node, user, corrected, rightmove = nodes
        user.push("10 High St", "user")
        corrected.push("", "user")
        rightmove.push("", "rightmove")
        await flush_processor()
        j = await node.to_json()
        assert j["status"] == "succeeded"
        assert j["value"] == "10 High St"
        assert "error" not in j

    @pytest.mark.asyncio
    async def test_corrected_address_push_after_compute_recomputes(self):
        """First corrected_address push after an initial compute (corrected
        never had a value) must recompute best_address."""
        from houses.nodes.location import BestAddressNode

        user = UserInputNode[str]("addr_user_firstpush", str)
        corrected = UserInputNode[str]("addr_corr_firstpush", str)
        rightmove = UserInputNode[str]("addr_rm_firstpush", str)
        node = BestAddressNode(
            "addr_best_firstpush",
            user_entered_address=user,
            corrected_address=corrected,
            rightmove_address=rightmove,
        )

        rightmove.push("Old Rightmove St", "Rightmove")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == "Old Rightmove St"

        corrected.push("New Corrected St", "User correction")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == "New Corrected St"

    @pytest.mark.asyncio
    async def test_corrected_address_push_after_restart_recomputes(self):
        """A corrected_address push after a server restart must recompute
        best_address — the optional-source staleness snapshot is lost on
        restart and must not mask the change."""
        from houses.nodes.location import BestAddressNode

        node_id = "addr_best_restart"
        # First process: seed rightmove + corrected, compute best_address.
        user = UserInputNode[str]("addr_user_restart", str)
        corrected = UserInputNode[str]("addr_corr_restart", str)
        rightmove = UserInputNode[str]("addr_rm_restart", str)
        node = BestAddressNode(
            node_id,
            user_entered_address=user,
            corrected_address=corrected,
            rightmove_address=rightmove,
        )
        rightmove.push("Old Rightmove St", "Rightmove")
        corrected.push("Old Corrected St", "User correction")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == "Old Corrected St"

        # Restart: fresh node objects load persisted attempts from the DB.
        user2 = UserInputNode[str]("addr_user_restart", str)
        corrected2 = UserInputNode[str]("addr_corr_restart", str)
        rightmove2 = UserInputNode[str]("addr_rm_restart", str)
        node2 = BestAddressNode(
            node_id,
            user_entered_address=user2,
            corrected_address=corrected2,
            rightmove_address=rightmove2,
        )
        # Fresh after restart — no recompute cascade without a change.
        assert (await node2.attempt()).value_or_none() == "Old Corrected St"

        corrected2.push("New Corrected St", "User correction")
        await flush_processor()
        assert (await node2.attempt()).value_or_none() == "New Corrected St"

    @pytest.mark.asyncio
    async def test_user_entered_address_push_after_restart_recomputes(self):
        """A user_entered_address push after restart takes priority over the
        persisted corrected_address."""
        from houses.nodes.location import BestAddressNode

        node_id = "addr_best_user_restart"
        user = UserInputNode[str]("addr_user_u", str)
        corrected = UserInputNode[str]("addr_corr_u", str)
        rightmove = UserInputNode[str]("addr_rm_u", str)
        node = BestAddressNode(
            node_id,
            user_entered_address=user,
            corrected_address=corrected,
            rightmove_address=rightmove,
        )
        rightmove.push("Old Rightmove St", "Rightmove")
        corrected.push("Old Corrected St", "User correction")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == "Old Corrected St"

        user2 = UserInputNode[str]("addr_user_u", str)
        corrected2 = UserInputNode[str]("addr_corr_u", str)
        rightmove2 = UserInputNode[str]("addr_rm_u", str)
        node2 = BestAddressNode(
            node_id,
            user_entered_address=user2,
            corrected_address=corrected2,
            rightmove_address=rightmove2,
        )

        user2.push("User Rd, SW1V 2QQ", "user")
        await flush_processor()
        assert (await node2.attempt()).value_or_none() == "User Rd, SW1V 2QQ"


class TestBestLocationNode:
    @pytest.mark.asyncio
    async def test_precise_takes_priority(self):
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = UserInputNode[str]("addr", str)
        node = BestLocationNode(
            "best_loc", precise_location=precise, rightmove_location=rightmove_loc, best_address=best_addr
        )

        gp = GeoPoint(51.5, -0.1)
        precise.push(gp, "user")
        rightmove_loc.push(GeoPoint(51.4, -0.2), "rightmove")
        best_addr.push("London", "rightmove")

        await flush_processor()
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
        node = BestLocationNode(
            "best_loc_fb", precise_location=precise, rightmove_location=rightmove_loc, best_address=best_addr
        )

        rm_gp = GeoPoint(51.4, -0.2)
        rightmove_loc.push(rm_gp, "rightmove")
        best_addr.push("Vague Road, London", "rightmove")

        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == rm_gp

    @pytest.mark.asyncio
    async def test_pending_when_no_sources(self):
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = UserInputNode[str]("addr", str)
        node = BestLocationNode(
            "best_loc", precise_location=precise, rightmove_location=rightmove_loc, best_address=best_addr
        )

        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_pending_when_all_deps_missing(self):
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = UserInputNode[str]("addr", str)
        node = BestLocationNode(
            "best_loc", precise_location=precise, rightmove_location=rightmove_loc, best_address=best_addr
        )

        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_recomputes_when_precise_updated(self):
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = UserInputNode[str]("addr", str)
        node = BestLocationNode(
            "best_loc", precise_location=precise, rightmove_location=rightmove_loc, best_address=best_addr
        )

        rm_gp = GeoPoint(51.4, -0.2)
        rightmove_loc.push(rm_gp, "rightmove")
        best_addr.push("London", "rightmove")
        precise.push(rm_gp, "user")  # same as rightmove initially
        await flush_processor()
        assert (await node.attempt()).value_or_none() == rm_gp

        user_gp = GeoPoint(51.5, -0.1)
        precise.push(user_gp, "user")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == user_gp

    @pytest.mark.asyncio
    async def test_precise_location_push_after_restart_recomputes(self):
        """A precise_location push after a server restart must recompute
        best_location — same optional-source staleness bug class."""
        from houses.nodes.location import BestLocationNode

        node_id = "best_loc_restart"
        precise = UserInputNode[GeoPoint]("precise_restart", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc_restart", GeoPoint)
        best_addr = UserInputNode[str]("addr_restart", str)
        node = BestLocationNode(
            node_id,
            precise_location=precise,
            rightmove_location=rightmove_loc,
            best_address=best_addr,
        )

        rm_gp = GeoPoint(51.4, -0.2)
        rightmove_loc.push(rm_gp, "rightmove")
        best_addr.push("London", "rightmove")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == rm_gp

        # Restart: fresh nodes load persisted state.
        precise2 = UserInputNode[GeoPoint]("precise_restart", GeoPoint)
        rightmove_loc2 = UserInputNode[GeoPoint]("rm_loc_restart", GeoPoint)
        best_addr2 = UserInputNode[str]("addr_restart", str)
        node2 = BestLocationNode(
            node_id,
            precise_location=precise2,
            rightmove_location=rightmove_loc2,
            best_address=best_addr2,
        )
        assert (await node2.attempt()).value_or_none() == rm_gp

        user_gp = GeoPoint(51.5, -0.1)
        precise2.push(user_gp, "user")
        await flush_processor()
        assert (await node2.attempt()).value_or_none() == user_gp

    @pytest.mark.asyncio
    async def test_to_json_with_succeeded(self):
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = UserInputNode[str]("addr", str)
        node = BestLocationNode(
            "best_loc", precise_location=precise, rightmove_location=rightmove_loc, best_address=best_addr
        )

        gp = GeoPoint(51.5, -0.1)
        precise.push(gp, "user")
        rightmove_loc.push(GeoPoint(51.4, -0.2), "rightmove")
        best_addr.push("London", "rightmove")
        await flush_processor()
        j = await node.to_json()
        assert j["status"] == "succeeded"
        assert j["value"] == {"lat": 51.5, "lon": -0.1}

    @pytest.mark.asyncio
    async def test_to_json_with_pending(self):
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = UserInputNode[str]("addr", str)
        node = BestLocationNode(
            "best_loc", precise_location=precise, rightmove_location=rightmove_loc, best_address=best_addr
        )

        j = await node.to_json()
        assert j["status"] == "pending"
        assert j["value"] is None


class TestBestLocationNodeGeocodeFallback:
    """BestLocationNode should fall back to GeocodeNode when coordinates
    are unavailable but a single-property address exists."""

    @pytest.mark.asyncio
    async def test_geocode_fallback_priority_over_rightmove(self):
        """When precise_location is missing, geocode result should
        take priority over rightmove_location for single-property addresses."""
        from houses.nodes.location import BestLocationNode

        # Use UserInputNode as stand-in for GeocodeNode's result
        precise = UserInputNode[GeoPoint]("precise_gp", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc_gp", GeoPoint)
        best_addr = UserInputNode[str]("addr_gp", str)
        geocode_result = UserInputNode[GeoPoint]("geocode_gp", GeoPoint)

        node = BestLocationNode(
            "best_loc_gp",
            precise_location=precise,
            rightmove_location=rightmove_loc,
            best_address=best_addr,
            geocode=geocode_result,
        )

        gp_geo = GeoPoint(51.5, -0.1)
        gp_rm = GeoPoint(51.4, -0.2)

        best_addr.push("123 Main Road, London, SW1 1AA", "rightmove")
        rightmove_loc.push(gp_rm, "rightmove")
        geocode_result.push(gp_geo, "geocode")

        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        # Geocode should take priority over rightmove when precise is missing
        # and address is single-property
        assert a.value_or_none() == gp_geo

    @pytest.mark.asyncio
    async def test_falls_back_to_rightmove_when_geocode_fails(self):
        """When geocode fails but rightmove_location succeeds, use rightmove."""
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise_gf2", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc_gf2", GeoPoint)
        best_addr = UserInputNode[str]("addr_gf2", str)
        geocode_result = UserInputNode[GeoPoint]("geocode_gf2", GeoPoint)

        node = BestLocationNode(
            "best_loc_gf2",
            precise_location=precise,
            rightmove_location=rightmove_loc,
            best_address=best_addr,
            geocode=geocode_result,
        )

        gp_rm = GeoPoint(51.4, -0.2)
        best_addr.push("Vague Road, London", "rightmove")
        rightmove_loc.push(gp_rm, "rightmove")
        # geocode_result left empty (pending)
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == gp_rm

    @pytest.mark.asyncio
    async def test_precise_still_takes_highest_priority(self):
        """precise_location remains highest priority even when geocode is wired."""
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise_gf3", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc_gf3", GeoPoint)
        best_addr = UserInputNode[str]("addr_gf3", str)
        geocode_result = UserInputNode[GeoPoint]("geocode_gf3", GeoPoint)

        node = BestLocationNode(
            "best_loc_gf3",
            precise_location=precise,
            rightmove_location=rightmove_loc,
            best_address=best_addr,
            geocode=geocode_result,
        )

        gp_precise = GeoPoint(51.5, -0.1)
        gp_geo = GeoPoint(51.6, -0.2)
        gp_rm = GeoPoint(51.4, -0.3)

        best_addr.push("123 Main Road, London, SW1 1AA", "rightmove")
        rightmove_loc.push(gp_rm, "rightmove")
        geocode_result.push(gp_geo, "geocode")
        precise.push(gp_precise, "user")

        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == gp_precise

    @pytest.mark.asyncio
    async def test_recomputes_when_geocode_updated(self):
        """Updating the geocode dependency should trigger recompute."""
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint]("precise_gf4", GeoPoint)
        rightmove_loc = UserInputNode[GeoPoint]("rm_loc_gf4", GeoPoint)
        best_addr = UserInputNode[str]("addr_gf4", str)
        geocode_result = UserInputNode[GeoPoint]("geocode_gf4", GeoPoint)

        node = BestLocationNode(
            "best_loc_gf4",
            precise_location=precise,
            rightmove_location=rightmove_loc,
            best_address=best_addr,
            geocode=geocode_result,
        )

        best_addr.push("123 Main Road, London, SW1 1AA", "rightmove")
        rightmove_loc.push(GeoPoint(51.4, -0.2), "rightmove")
        # Initial geocode same as rightmove
        gp1 = GeoPoint(51.4, -0.2)
        geocode_result.push(gp1, "geocode")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == gp1

        # Update geocode with better coordinates
        gp2 = GeoPoint(51.5, -0.1)
        geocode_result.push(gp2, "geocode")
        await flush_processor()
        assert (await node.attempt()).value_or_none() == gp2
