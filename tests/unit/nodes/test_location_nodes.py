from __future__ import annotations

from dag.attempt import Provenance
from dag.source_node import SourceNode
from houses.geo import GeoPoint


class TestBestAddressNode:
    """BestAddressNode selects corrected_address over rightmove_address."""

    def test_corrected_takes_priority(self):
        from houses.nodes.location import BestAddressNode

        corrected = SourceNode[str]("corrected", str)
        rightmove = SourceNode[str]("rightmove", str)
        node = BestAddressNode("best_addr", corrected_address=corrected,
                               rightmove_address=rightmove)

        corrected.push("User Rd", Provenance("user"))
        rightmove.push("RM Rd", Provenance("rightmove"))

        a = node.attempt()
        assert a.is_succeeded
        assert a.value_or_none() == "User Rd"

    def test_fallback_to_rightmove(self):
        from houses.nodes.location import BestAddressNode

        corrected = SourceNode[str]("corrected", str)
        rightmove = SourceNode[str]("rightmove", str)
        node = BestAddressNode("best_addr", corrected_address=corrected,
                               rightmove_address=rightmove)

        rightmove.push("RM Rd", Provenance("rightmove"))

        a = node.attempt()
        assert a.is_succeeded
        assert a.value_or_none() == "RM Rd"

    def test_both_impossible(self):
        from houses.nodes.location import BestAddressNode

        corrected = SourceNode[str]("corrected", str)
        rightmove = SourceNode[str]("rightmove", str)
        node = BestAddressNode("best_addr", corrected_address=corrected,
                               rightmove_address=rightmove)

        a = node.attempt()
        assert not a.is_succeeded
        assert "corrected_address" in a._error
        assert "rightmove_address" in a._error

    def test_recomputes_when_corrected_changes(self):
        from houses.nodes.location import BestAddressNode

        corrected = SourceNode[str]("corrected", str)
        rightmove = SourceNode[str]("rightmove", str)
        node = BestAddressNode("best_addr", corrected_address=corrected,
                               rightmove_address=rightmove)

        rightmove.push("RM Rd", Provenance("rightmove"))
        assert node.attempt().value_or_none() == "RM Rd"

        corrected.push("User Rd", Provenance("user"))
        assert node.attempt().value_or_none() == "User Rd"

    def test_to_json_shape(self):
        from houses.nodes.location import BestAddressNode

        corrected = SourceNode[str]("corrected", str)
        rightmove = SourceNode[str]("rightmove", str)
        node = BestAddressNode("best_addr", corrected_address=corrected,
                               rightmove_address=rightmove)

        corrected.push("10 High St", Provenance("user"))
        j = node.to_json()
        assert j["succeeded"] is True
        assert j["value"] == "10 High St"
        assert j["error"] is None


class TestBestLocationNode:
    """BestLocationNode selects precise > rightmove, with geocode fallback."""

    def test_precise_takes_priority(self):
        from houses.nodes.location import BestLocationNode

        precise = SourceNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = SourceNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = SourceNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        gp = GeoPoint(51.5, -0.1)
        precise.push(gp, Provenance("user"))

        a = node.attempt()
        assert a.is_succeeded
        assert a.value_or_none() == gp

    def test_rightmove_used_when_precise_missing_and_vague_address(self):
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

        a = node.attempt()
        assert a.is_succeeded
        assert a.value_or_none() == rm_gp

    def test_impossible_when_no_sources(self):
        from houses.nodes.location import BestLocationNode

        precise = SourceNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = SourceNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = SourceNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        a = node.attempt()
        assert not a.is_succeeded
        assert "best_loc" in a._error

    def test_impossible_mentions_all_failed_deps(self):
        from houses.nodes.location import BestLocationNode

        precise = SourceNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = SourceNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = SourceNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        a = node.attempt()
        assert "precise_location" in a._error
        assert "rightmove_location" in a._error
        assert "best_address" in a._error

    def test_recomputes_when_precise_updated(self):
        from houses.nodes.location import BestLocationNode

        precise = SourceNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = SourceNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = SourceNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        rm_gp = GeoPoint(51.4, -0.2)
        rightmove_loc.push(rm_gp, Provenance("rightmove"))
        assert node.attempt().value_or_none() == rm_gp

        user_gp = GeoPoint(51.5, -0.1)
        precise.push(user_gp, Provenance("user"))
        assert node.attempt().value_or_none() == user_gp

    def test_to_json_with_succeeded(self):
        from houses.nodes.location import BestLocationNode

        precise = SourceNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = SourceNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = SourceNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        gp = GeoPoint(51.5, -0.1)
        precise.push(gp, Provenance("user"))
        j = node.to_json()
        assert j["succeeded"] is True
        assert j["value"] == {"lat": 51.5, "lon": -0.1}

    def test_to_json_with_impossible(self):
        from houses.nodes.location import BestLocationNode

        precise = SourceNode[GeoPoint]("precise", GeoPoint)
        rightmove_loc = SourceNode[GeoPoint]("rm_loc", GeoPoint)
        best_addr = SourceNode[str]("addr", str)
        node = BestLocationNode("best_loc", precise_location=precise,
                                rightmove_location=rightmove_loc,
                                best_address=best_addr)

        j = node.to_json()
        assert j["succeeded"] is False
        assert j["value"] is None
