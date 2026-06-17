from __future__ import annotations

from dag.source_node import SourceNode
from houses.geo import GeoPoint


class TestBootstrapFromRow:
    def test_pushes_address(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "rightmove_address": SourceNode[str]("rightmove_address", str),
        }
        row = {"Address": "10 High St, London SW1V 2QQ"}
        bootstrap_from_row(row, sources)
        assert sources["rightmove_address"].attempt().value_or_none() == "10 High St, London SW1V 2QQ"

    def test_pushes_url(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "rightmove_url": SourceNode[str]("rightmove_url", str),
        }
        row = {"Rightmove URL": "https://www.rightmove.co.uk/properties/12345"}
        bootstrap_from_row(row, sources)
        assert sources["rightmove_url"].attempt().value_or_none() == "https://www.rightmove.co.uk/properties/12345"

    def test_pushes_bedrooms(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "rightmove_bedrooms": SourceNode[str]("rightmove_bedrooms", str),
        }
        row = {"Bedrooms": "3"}
        bootstrap_from_row(row, sources)
        assert sources["rightmove_bedrooms"].attempt().value_or_none() == "3"

    def test_pushes_price(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "rightmove_price": SourceNode[str]("rightmove_price", str),
        }
        row = {"Price (£)": "450000"}
        bootstrap_from_row(row, sources)
        assert sources["rightmove_price"].attempt().value_or_none() == "450000"

    def test_pushes_rightmove_location(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "rightmove_location": SourceNode[GeoPoint]("rightmove_location", GeoPoint),
        }
        row = {
            "Approx Latitude (est)": "51.5",
            "Approx Longitude (est)": "-0.1",
            "Postcode": "SW1V 2QQ",
        }
        bootstrap_from_row(row, sources)
        a = sources["rightmove_location"].attempt()
        assert a.is_succeeded
        assert a.value_or_none() == GeoPoint(51.5, -0.1)

    def test_skips_rightmove_location_when_coords_invalid(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "rightmove_location": SourceNode[GeoPoint]("rightmove_location", GeoPoint),
        }
        row = {
            "Approx Latitude (est)": "not-a-number",
            "Approx Longitude (est)": "-0.1",
        }
        bootstrap_from_row(row, sources)
        assert not sources["rightmove_location"].attempt().is_succeeded

    def test_pushes_precise_location(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "precise_location": SourceNode[GeoPoint]("precise_location", GeoPoint),
        }
        row = {
            "Actual Latitude": "51.6",
            "Actual Longitude": "-0.2",
            "Postcode": "SW1V 2QQ",
        }
        bootstrap_from_row(row, sources)
        a = sources["precise_location"].attempt()
        assert a.is_succeeded
        assert a.value_or_none() == GeoPoint(51.6, -0.2)

    def test_pushes_corrected_address_with_postcode(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "corrected_address": SourceNode[str]("corrected_address", str),
        }
        row = {
            "Address": "10 High St, London",
            "Postcode": "SW1V 2QQ",
        }
        bootstrap_from_row(row, sources)
        a = sources["corrected_address"].attempt()
        assert a.is_succeeded
        assert "SW1V 2QQ" in a.value_or_none()

    def test_all_sources_integration(self):
        from houses.nodes.bootstrap import bootstrap_from_row
        from houses.nodes.location import BestAddressNode, BestLocationNode

        precise = SourceNode[GeoPoint]("precise_location", GeoPoint)
        corrected = SourceNode[str]("corrected_address", str)
        rightmove_addr = SourceNode[str]("rightmove_address", str)
        rightmove_loc = SourceNode[GeoPoint]("rightmove_location", GeoPoint)

        row = {
            "Address": "10 High St, London",
            "Postcode": "SW1V 2QQ",
            "Actual Latitude": "51.6",
            "Actual Longitude": "-0.2",
            "Approx Latitude (est)": "51.5",
            "Approx Longitude (est)": "-0.1",
        }
        sources = {
            "rightmove_address": rightmove_addr,
            "rightmove_location": rightmove_loc,
            "precise_location": precise,
            "corrected_address": corrected,
        }
        bootstrap_from_row(row, sources)

        # Precise takes priority
        best_loc = BestLocationNode(
            "best_loc",
            precise_location=precise,
            rightmove_location=rightmove_loc,
            best_address=BestAddressNode(
                "best_addr",
                corrected_address=corrected,
                rightmove_address=rightmove_addr,
            ),
        )
        a = best_loc.attempt()
        assert a.is_succeeded
        assert a.value_or_none() == GeoPoint(51.6, -0.2)

    def test_sets_provenance_labels(self):
        from houses.nodes.bootstrap import PROVENANCE_LABELS, bootstrap_from_row

        sources = {
            "rightmove_address": SourceNode[str]("rightmove_address", str),
        }
        row = {"Address": "10 High St"}
        bootstrap_from_row(row, sources)
        a = sources["rightmove_address"].attempt()
        assert a.provenance.label == PROVENANCE_LABELS["rightmove_address"]
