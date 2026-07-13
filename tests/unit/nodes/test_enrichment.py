from __future__ import annotations

import pytest

from dag.user_input_node import UserInputNode


class TestEpcNode:
    @pytest.mark.asyncio
    async def test_impossible_without_address(self):
        from houses.nodes.epc_node import EpcNode

        addr = UserInputNode[str]("addr_epc", str)
        pc = UserInputNode[str]("pc_epc", str)
        node = EpcNode("epc", best_address=addr, postcode_node=pc)
        a = await node.attempt()
        assert not a.succeeded


class TestCouncilTaxNode:
    @pytest.mark.asyncio
    async def test_impossible_without_postcode(self):
        from houses.nodes.epc_node import CouncilTaxNode

        addr = UserInputNode[str]("addr_ct", str)
        pc = UserInputNode[str]("pc_ct", str)
        node = CouncilTaxNode("ct", best_address=addr, postcode_node=pc)
        a = await node.attempt()
        assert not a.succeeded

    @pytest.mark.asyncio
    async def test_impossible_without_address(self):
        from houses.nodes.epc_node import CouncilTaxNode

        addr = UserInputNode[str]("addr_ct2", str)
        pc = UserInputNode[str]("pc_ct2", str)
        node = CouncilTaxNode("ct2", best_address=addr, postcode_node=pc)
        a = await node.attempt()
        assert not a.succeeded


class TestWalkabilityNode:
    @pytest.mark.asyncio
    async def test_impossible_without_location(self):
        from houses.nodes.area import WalkabilityNode

        loc = UserInputNode[dict]("loc_w", dict)
        addr = UserInputNode[str]("addr_w", str)
        node = WalkabilityNode("wlk", best_location=loc, best_address=addr)
        a = await node.attempt()
        assert not a.succeeded


class TestTownDescNode:
    @pytest.mark.asyncio
    async def test_impossible_without_location(self):
        from houses.nodes.area import TownDescNode

        loc = UserInputNode[dict]("loc_td", dict)
        node = TownDescNode("td", best_location=loc)
        a = await node.attempt()
        assert not a.succeeded


class TestGeocodeNode:
    @pytest.mark.asyncio
    async def test_impossible_without_address(self):
        from houses.nodes.geocode import GeocodeNode

        addr = UserInputNode[str]("addr_gc", str)
        node = GeocodeNode("gc", best_address=addr)
        a = await node.attempt()
        assert not a.succeeded


class TestParkAndRideAugmentNode:
    @pytest.mark.asyncio
    async def test_impossible_without_transit(self):
        from houses.nodes.park_and_ride import ParkAndRideAugmentNode

        transit = UserInputNode[dict]("t_pr", dict)
        node = ParkAndRideAugmentNode("pr", transit_node=transit)
        a = await node.attempt()
        assert not a.succeeded
