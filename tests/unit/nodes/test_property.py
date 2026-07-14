from __future__ import annotations

import pytest

from dag.derived_node import flush_processor
from tests.unit.conftest import flush_all
from houses.geo import GeoPoint


class TestProperty:
    def test_creates_user_input_nodes(self):
        from houses.nodes.property import PropertyNodes

        prop = PropertyNodes("prop123")
        assert prop.rid == "prop123"
        assert prop.rightmove_address is not None
        assert prop.rightmove_location is not None
        assert prop.precise_location is not None
        assert prop.corrected_address is not None

    def test_creates_derived_nodes(self):
        from houses.nodes.property import PropertyNodes

        prop = PropertyNodes("prop123")
        assert prop.best_address is not None
        assert prop.best_location is not None

    def test_changed_fires_when_source_updates(self):
        from houses.nodes.property import PropertyNodes

        prop = PropertyNodes("prop123")
        received = []
        prop.changed.connect(lambda: received.append("changed"))

        prop.precise_location.push(GeoPoint(51.5, -0.1), "user")

        assert len(received) >= 1

    @pytest.mark.asyncio
    async def test_best_location_uses_precise(self):
        from houses.nodes.property import PropertyNodes

        prop = PropertyNodes("prop123")
        gp = GeoPoint(51.5, -0.1)
        prop.precise_location.push(gp, "user")
        prop.rightmove_location.push(GeoPoint(51.4, -0.2), "rightmove")
        prop.user_entered_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        prop.corrected_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        prop.rightmove_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        await flush_processor()
        await flush_processor()

        a = await prop.best_location.attempt()
        assert a.succeeded
        assert a.value_or_none() == gp

    @pytest.mark.asyncio
    async def test_to_json_includes_location(self):
        from houses.nodes.property import PropertyNodes

        prop = PropertyNodes("prop123")
        gp = GeoPoint(51.5, -0.1)
        prop.precise_location.push(gp, "user")
        prop.rightmove_location.push(GeoPoint(51.4, -0.2), "rightmove")
        prop.user_entered_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        prop.corrected_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        prop.rightmove_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        await flush_processor()
        await flush_processor()

        j = await prop.to_json()
        assert j["rid"] == "prop123"
        assert j["best_location"]["status"] == "succeeded"
        assert j["best_location"]["value"] == {"lat": 51.5, "lon": -0.1}
