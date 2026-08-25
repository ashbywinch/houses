"""Tests for the DAG enrichment flow — node wiring, persistence, and API.

Replaces the old ``test_enrichment_flow.py`` that depended on the deleted
``houses.model.persistence``, ``houses.model.property``, and
``houses.model.resolver`` modules.  Every test now exercises the DAG
(``dag/``) backed processor pipeline.
"""

from __future__ import annotations

import pytest
from money import Money

from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.geopoint import GeoPoint

RID = "test123"


# ── BestLocationNode integration ─────────────────────────────────────


class TestBestLocationFlow:
    """BestLocationNode wired to its source nodes — persistence, reload, re-enrich."""

    @pytest.mark.asyncio
    async def test_value_persists_across_node_recreation(self):
        """A computed BestLocationNode value survives re-creating the node
        (loaded from the DAG persistence layer)."""
        from houses.nodes.location import BestLocationNode

        # Phase 1: push data and flush the processor.
        precise = UserInputNode[GeoPoint](f"{RID}/flow_precise_persist", GeoPoint)
        rm_loc = UserInputNode[GeoPoint](f"{RID}/flow_rm_loc_persist", GeoPoint)
        best_addr = UserInputNode[str](f"{RID}/flow_addr_persist", str)

        node = BestLocationNode(
            f"{RID}/best_loc_persist",
            precise_location=precise,
            rightmove_location=rm_loc,
            best_address=best_addr,
        )

        gp = GeoPoint(51.5, -0.1)
        rm_loc.push(gp, "Rightmove map")
        best_addr.push("10 High St", "Rightmove")
        await flush_processor()
        await flush_processor()

        j = await node.to_json()
        assert j["status"] == "succeeded"
        assert j["value"] == {"lat": 51.5, "lon": -0.1}

        # Phase 2: re-create nodes with the same IDs — should reload from DB.
        precise2 = UserInputNode[GeoPoint](f"{RID}/flow_precise_persist", GeoPoint)
        rm_loc2 = UserInputNode[GeoPoint](f"{RID}/flow_rm_loc_persist", GeoPoint)
        best_addr2 = UserInputNode[str](f"{RID}/flow_addr_persist", str)

        node2 = BestLocationNode(
            f"{RID}/best_loc_persist",
            precise_location=precise2,
            rightmove_location=rm_loc2,
            best_address=best_addr2,
        )

        j2 = await node2.to_json()
        assert j2["status"] == "succeeded"
        assert j2["value"] == {"lat": 51.5, "lon": -0.1}

    @pytest.mark.asyncio
    async def test_impossible_when_no_location_sources(self):
        """Without any location source, best_location stays impossible
        even when an address exists."""
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint](f"{RID}/flow_precise_none", GeoPoint)
        rm_loc = UserInputNode[GeoPoint](f"{RID}/flow_rm_loc_none", GeoPoint)
        best_addr = UserInputNode[str](f"{RID}/flow_addr_none", str)

        best_addr.push("10 High St, London", "Rightmove")
        node = BestLocationNode(
            f"{RID}/best_loc_none",
            precise_location=precise,
            rightmove_location=rm_loc,
            best_address=best_addr,
        )
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.impossible

    @pytest.mark.asyncio
    async def test_recomputes_on_rightmove_location_update(self):
        """Pushing a new rightmove_location triggers recomputation."""
        from houses.nodes.location import BestLocationNode

        precise = UserInputNode[GeoPoint](f"{RID}/flow_precise_recomp", GeoPoint)
        rm_loc = UserInputNode[GeoPoint](f"{RID}/flow_rm_loc_recomp", GeoPoint)
        best_addr = UserInputNode[str](f"{RID}/flow_addr_recomp", str)

        node = BestLocationNode(
            f"{RID}/best_loc_recomp",
            precise_location=precise,
            rightmove_location=rm_loc,
            best_address=best_addr,
        )

        gp1 = GeoPoint(51.4, -0.2)
        best_addr.push("Some Road, London", "Rightmove")
        rm_loc.push(gp1, "Rightmove map")
        await flush_processor()
        await flush_processor()
        assert (await node.attempt()).value_or_none() == gp1

        gp2 = GeoPoint(51.5, -0.1)
        rm_loc.push(gp2, "Rightmove map")
        await flush_processor()
        await flush_processor()
        assert (await node.attempt()).value_or_none() == gp2


# ── BestAddressNode integration ──────────────────────────────────────


class TestBestAddressFlow:
    """BestAddressNode priority, persistence, and re-enrichment."""

    @pytest.mark.asyncio
    async def test_corrected_address_overrides_rightmove(self):
        """corrected_address has higher priority than rightmove_address."""
        from houses.nodes.location import BestAddressNode

        user = UserInputNode[str](f"{RID}/flow_user_addr", str)
        corrected = UserInputNode[str](f"{RID}/flow_corrected_addr", str)
        rightmove = UserInputNode[str](f"{RID}/flow_rm_addr", str)

        node = BestAddressNode(
            f"{RID}/best_addr_flow",
            user_entered_address=user,
            corrected_address=corrected,
            rightmove_address=rightmove,
        )

        rightmove.push("Rightmove St", "Rightmove")
        corrected.push("User Rd", "User correction")
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == "User Rd"

    @pytest.mark.asyncio
    async def test_derived_value_persisted_after_resolve(self):
        """After flush, the derived address is stored in the persistence layer
        and can be loaded by a freshly created node with the same ID."""
        from houses.nodes.location import BestAddressNode

        user = UserInputNode[str](f"{RID}/flow_user_persist", str)
        corrected = UserInputNode[str](f"{RID}/flow_corrected_persist", str)
        rightmove = UserInputNode[str](f"{RID}/flow_rm_persist", str)

        node = BestAddressNode(
            f"{RID}/best_addr_persist",
            user_entered_address=user,
            corrected_address=corrected,
            rightmove_address=rightmove,
        )
        rightmove.push("10 High St", "Rightmove")
        await flush_processor()
        await flush_processor()

        j = await node.to_json()
        assert j["status"] == "succeeded"
        assert j["value"] == "10 High St"

        # Re-create node — should load persisted result.
        user2 = UserInputNode[str](f"{RID}/flow_user_persist", str)
        corrected2 = UserInputNode[str](f"{RID}/flow_corrected_persist", str)
        rightmove2 = UserInputNode[str](f"{RID}/flow_rm_persist", str)

        node2 = BestAddressNode(
            f"{RID}/best_addr_persist",
            user_entered_address=user2,
            corrected_address=corrected2,
            rightmove_address=rightmove2,
        )
        j2 = await node2.to_json()
        assert j2["status"] == "succeeded"
        assert j2["value"] == "10 High St"

    @pytest.mark.asyncio
    async def test_re_enrichment_updates_derived_value(self):
        """Pushing a new rightmove_address causes the derived node to re-compute."""
        from houses.nodes.location import BestAddressNode

        user = UserInputNode[str](f"{RID}/flow_user_reenrich", str)
        corrected = UserInputNode[str](f"{RID}/flow_corrected_reenrich", str)
        rightmove = UserInputNode[str](f"{RID}/flow_rm_reenrich", str)

        node = BestAddressNode(
            f"{RID}/best_addr_reenrich",
            user_entered_address=user,
            corrected_address=corrected,
            rightmove_address=rightmove,
        )

        rightmove.push("Old address", "Rightmove")
        await flush_processor()
        await flush_processor()
        assert (await node.attempt()).value_or_none() == "Old address"

        rightmove.push("New address", "Rightmove")
        await flush_processor()
        await flush_processor()
        assert (await node.attempt()).value_or_none() == "New address"


# ── API endpoint integration ─────────────────────────────────────────


class TestEnrichmentApi:
    """DAG-backed endpoints: register a PropertyNodes and query via the API router."""

    @pytest.mark.asyncio
    async def test_get_property_returns_expected_fields(self):
        """GET /api/properties/{rid} returns DAG node JSON for registered property."""
        from houses.nodes.property_nodes import PropertyNodes
        from houses.property_registry import register_property
        from houses.web.api_router import get_property

        prop = PropertyNodes(RID)
        register_property(RID, prop)

        prop.rightmove_address.push("10 High St", "Rightmove")
        prop.rightmove_bedrooms.push("3", "Rightmove")
        prop.rightmove_price.push(Money("250000", "GBP"), "Rightmove")
        await flush_processor()
        await flush_processor()

        result = await get_property(RID)
        assert isinstance(result, dict)
        assert result.get("best_address", {}).get("value") == "10 High St"
        assert result.get("rightmove_bedrooms", {}).get("value") == "3"
        assert "rid" in result

    @pytest.mark.asyncio
    async def test_get_property_404_for_unknown_rid(self):
        """GET /api/properties/{rid} returns 404 when property not registered."""
        from fastapi import HTTPException

        from houses.web.api_router import get_property

        with pytest.raises(HTTPException) as exc_info:
            await get_property("unknown123")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_staleness_check_returns_fresh_when_all_deps_met(self):
        """After seeding data and flushing, staleness reports a fresh node."""
        from houses.nodes.property_nodes import PropertyNodes
        from houses.property_registry import register_property
        from houses.web.api_router import staleness_check

        prop = PropertyNodes(RID)
        register_property(RID, prop)

        prop.rightmove_address.push("10 High St", "Rightmove")
        prop.rightmove_bedrooms.push("3", "Rightmove")
        prop.rightmove_price.push(Money("250000", "GBP"), "Rightmove")
        await flush_processor()
        await flush_processor()

        result = await staleness_check(RID, nodes="best_address")
        assert result["fresh"]
        nodes = result["nodes"]
        assert isinstance(nodes, dict)
        assert not nodes.get("best_address", True)

    @pytest.mark.asyncio
    async def test_detail_includes_enriched_nodes(self):
        """GET /api/properties/{rid}/detail includes school / commute / cost nodes."""
        from houses.nodes.property_nodes import PropertyNodes
        from houses.property_registry import register_property
        from houses.web.api_router import get_property_detail

        prop = PropertyNodes(RID)
        register_property(RID, prop)

        prop.rightmove_address.push("10 High St, Southall, UB2 5AD", "Rightmove")
        prop.rightmove_bedrooms.push("3", "Rightmove")
        prop.rightmove_price.push(Money("250000", "GBP"), "Rightmove")
        await flush_processor()
        await flush_processor()

        detail = await get_property_detail(RID)
        assert detail["best_address"]["status"] == "succeeded"
        assert detail["best_address"]["value"] == "10 High St, Southall, UB2 5AD"
        assert detail["location"]["best_location"]["status"] == "succeeded"
        assert detail["location"]["best_location"]["value"] == {"lat": 51.5, "lon": -0.1}

    @pytest.mark.asyncio
    async def test_list_properties_returns_registered_rid(self):
        """A registered property appears in the property registry."""
        from houses.nodes.property_nodes import PropertyNodes
        from houses.property_registry import list_properties as registry_list
        from houses.property_registry import register_property

        prop = PropertyNodes(RID)
        register_property(RID, prop)

        rids = registry_list()
        assert RID in rids


# ── Bootstrap / import flow ──────────────────────────────────────────


class TestEnrichmentBootstrap:
    """Simulate the first-view import flow — push source values and verify
    the DAG resolves derived nodes correctly."""

    @pytest.mark.asyncio
    async def test_import_noop_when_no_rightmove_data(self):
        """Without any source pushes, derived nodes remain pending."""
        from houses.nodes.location import BestAddressNode

        user = UserInputNode[str](f"{RID}/bs_user", str)
        corrected = UserInputNode[str](f"{RID}/bs_corrected", str)
        rightmove = UserInputNode[str](f"{RID}/bs_rm", str)
        node = BestAddressNode(
            f"{RID}/bs_best_addr",
            user_entered_address=user,
            corrected_address=corrected,
            rightmove_address=rightmove,
        )

        # Nothing pushed — hard dep rightmove_address is missing.
        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_import_seeds_rightmove_address_and_resolves(self):
        """Push rightmove_address and verify best_address picks it up."""
        from houses.nodes.location import BestAddressNode

        user = UserInputNode[str](f"{RID}/bs_user2", str)
        corrected = UserInputNode[str](f"{RID}/bs_corrected2", str)
        rightmove = UserInputNode[str](f"{RID}/bs_rm2", str)
        node = BestAddressNode(
            f"{RID}/bs_best_addr2",
            user_entered_address=user,
            corrected_address=corrected,
            rightmove_address=rightmove,
        )

        rightmove.push("Imported Address", "Rightmove")
        await flush_processor()
        await flush_processor()

        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == "Imported Address"

    @pytest.mark.asyncio
    async def test_push_enriched_property_seeds_nodes(self):
        """push_enriched_property correctly pushes to the right UserInputNodes."""
        from houses.nodes.cutover import push_enriched_property
        from houses.nodes.property_nodes import PropertyNodes

        prop = PropertyNodes(RID * 2)

        # Create an EnrichedProperty (the old data shape).
        from houses.property import EnrichedProperty

        enriched = EnrichedProperty(
            url="https://rightmove.co.uk/properties/999",
            address="Pembroke Avenue, Hersham, KT12",
            postcode="KT12 4NT",
            bedrooms=3,
            price=300000,  # type: ignore[arg-type]  # why: legacy bare-int price is the fixture — push_enriched_property's non-Money wrap branch (isinstance guard → Money(str(price))) is the behaviour under test
            approx_latitude=None,
            approx_longitude=None,
        )

        push_enriched_property(
            RID * 2,
            enriched,
            {
                "rightmove_address": prop.rightmove_address,
                "rightmove_url": prop.rightmove_url,
                "rightmove_bedrooms": prop.rightmove_bedrooms,
                "rightmove_price": prop.rightmove_price,
                "rightmove_location": prop.rightmove_location,
            },
        )

        assert (await prop.rightmove_address.attempt()).value_or_none() == "Pembroke Avenue, Hersham, KT12"
        assert (await prop.rightmove_bedrooms.attempt()).value_or_none() == "3"
        assert (await prop.rightmove_price.attempt()).value_or_none() == Money("300000", "GBP")
        assert (await prop.rightmove_url.attempt()).value_or_none() == "https://rightmove.co.uk/properties/999"

    @pytest.mark.asyncio
    async def test_import_with_approx_location(self):
        """When approx lat/lng is available, rightmove_location is set and
        best_location picks it up (no GeocodeNode in the chain)."""
        from houses.nodes.cutover import push_enriched_property
        from houses.nodes.location import BestLocationNode

        # Build BestLocationNode *without* a GeocodeNode to isolate the
        # approx→rightmove_location flow.
        rm_addr = UserInputNode[str](f"{RID}/bs_approx_addr", str)
        rm_loc = UserInputNode[GeoPoint](f"{RID}/bs_approx_rm_loc", GeoPoint)
        precise = UserInputNode[GeoPoint](f"{RID}/bs_approx_precise", GeoPoint)

        node = BestLocationNode(
            f"{RID}/bs_approx_best_loc",
            precise_location=precise,
            rightmove_location=rm_loc,
            best_address=rm_addr,
        )

        from houses.property import EnrichedProperty

        enriched = EnrichedProperty(
            url="",
            address="Some Road, Hersham",
            postcode="KT12",
            bedrooms=None,  # type: ignore[arg-type]  # why: push_enriched_property guards `enriched.bedrooms is not None`; None keeps rightmove_bedrooms unpushed (default 0 would wrongly seed it)
            price=None,  # type: ignore[arg-type]  # why: same guard on price — None keeps rightmove_price unpushed in this location-only test
            approx_latitude=51.37,
            approx_longitude=-0.4,
        )
        push_enriched_property(
            "test_approx",
            enriched,
            {
                "rightmove_address": rm_addr,
                "rightmove_location": rm_loc,
            },
        )
        rm_addr.push("Some Road, Hersham", "Rightmove")

        await flush_processor()
        await flush_processor()

        j = await node.to_json()
        assert j["status"] == "succeeded"
        assert j["value"] == {"lat": 51.37, "lon": -0.4}

    @pytest.mark.asyncio
    async def test_precise_location_overrides_approx(self):
        """When both approx (rightmove_location) and precise_location are set,
        precise wins."""
        from houses.nodes.property_nodes import PropertyNodes

        prop = PropertyNodes(f"{RID}_precise")

        prop.rightmove_address.push("Some Road, Hersham", "Rightmove")
        prop.rightmove_location.push(GeoPoint(51.37, -0.4), "Rightmove map")
        prop.precise_location.push(GeoPoint(51.38, -0.41), "User location")

        await flush_processor()
        await flush_processor()

        a = await prop.best_location.attempt()
        assert a.succeeded
        assert a.value_or_none() == GeoPoint(51.38, -0.41)
