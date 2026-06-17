from __future__ import annotations

from dag.attempt import Provenance
from houses.geo import GeoPoint


class TestProperty:
    def test_creates_source_nodes(self):
        from houses.nodes.property import PropertyNodes

        prop = PropertyNodes("prop123")
        assert prop.rid == "prop123"
        assert prop.rightmove_address is not None
        assert prop.rightmove_location is not None
        assert prop.precise_location is not None
        assert prop.corrected_address is not None

    def test_creates_computed_nodes(self):
        from houses.nodes.property import PropertyNodes

        prop = PropertyNodes("prop123")
        assert prop.best_address is not None
        assert prop.best_location is not None

    def test_changed_fires_when_source_updates(self):
        from houses.nodes.property import PropertyNodes

        prop = PropertyNodes("prop123")
        received = []
        prop.changed.connect(lambda: received.append("changed"))

        prop.precise_location.push(GeoPoint(51.5, -0.1), Provenance("user"))

        assert len(received) >= 1

    def test_best_location_uses_precise(self):
        from houses.nodes.property import PropertyNodes

        prop = PropertyNodes("prop123")
        gp = GeoPoint(51.5, -0.1)
        prop.precise_location.push(gp, Provenance("user"))

        a = prop.best_location.attempt()
        assert a.is_succeeded
        assert a.value_or_none() == gp

    def test_to_json_includes_location(self):
        from houses.nodes.property import PropertyNodes

        prop = PropertyNodes("prop123")
        gp = GeoPoint(51.5, -0.1)
        prop.precise_location.push(gp, Provenance("user"))

        j = prop.to_json()
        assert j["rid"] == "prop123"
        assert j["best_location"]["succeeded"] is True
        assert j["best_location"]["value"] == {"lat": 51.5, "lon": -0.1}
