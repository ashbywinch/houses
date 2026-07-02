from __future__ import annotations

import pytest

from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint


class TestBootstrapFromRow:
    @pytest.mark.asyncio
    async def test_pushes_address(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "rightmove_address": UserInputNode[str]("rightmove_address", str),
        }
        row = {"Address": "10 High St, London SW1V 2QQ"}
        bootstrap_from_row(row, sources)
        a = await sources["rightmove_address"].attempt()
        assert a.value_or_none() == "10 High St, London SW1V 2QQ"

    @pytest.mark.asyncio
    async def test_pushes_url(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "rightmove_url": UserInputNode[str]("rightmove_url", str),
        }
        row = {"Rightmove URL": "https://www.rightmove.co.uk/properties/12345"}
        bootstrap_from_row(row, sources)
        a = await sources["rightmove_url"].attempt()
        assert a.value_or_none() == "https://www.rightmove.co.uk/properties/12345"

    @pytest.mark.asyncio
    async def test_pushes_bedrooms(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "rightmove_bedrooms": UserInputNode[str]("rightmove_bedrooms", str),
        }
        row = {"Bedrooms": "3"}
        bootstrap_from_row(row, sources)
        a = await sources["rightmove_bedrooms"].attempt()
        assert a.value_or_none() == "3"

    @pytest.mark.asyncio
    async def test_pushes_price(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "rightmove_price": UserInputNode[str]("rightmove_price", str),
        }
        row = {"Price (£)": "450000"}
        bootstrap_from_row(row, sources)
        a = await sources["rightmove_price"].attempt()
        assert a.value_or_none() == "450000"

    @pytest.mark.asyncio
    async def test_pushes_rightmove_location(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "rightmove_location": UserInputNode[GeoPoint]("rightmove_location", GeoPoint),
        }
        row = {
            "Approx Latitude (est)": "51.5",
            "Approx Longitude (est)": "-0.1",
            "Postcode": "SW1V 2QQ",
        }
        bootstrap_from_row(row, sources)
        a = await sources["rightmove_location"].attempt()
        assert a.succeeded
        assert a.value_or_none() == GeoPoint(51.5, -0.1)

    @pytest.mark.asyncio
    async def test_skips_rightmove_location_when_coords_invalid(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "rightmove_location": UserInputNode[GeoPoint]("rightmove_location", GeoPoint),
        }
        row = {
            "Approx Latitude (est)": "not-a-number",
            "Approx Longitude (est)": "-0.1",
        }
        bootstrap_from_row(row, sources)
        assert not (await sources["rightmove_location"].attempt()).succeeded

    @pytest.mark.asyncio
    async def test_pushes_precise_location(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "precise_location": UserInputNode[GeoPoint]("precise_location", GeoPoint),
        }
        row = {
            "Actual Latitude": "51.6",
            "Actual Longitude": "-0.2",
            "Postcode": "SW1V 2QQ",
        }
        bootstrap_from_row(row, sources)
        a = await sources["precise_location"].attempt()
        assert a.succeeded
        assert a.value_or_none() == GeoPoint(51.6, -0.2)

    @pytest.mark.asyncio
    async def test_pushes_corrected_address_with_postcode(self):
        """When address has no trailing outcode, corrected_address is still pushed."""
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "corrected_address": UserInputNode[str]("corrected_address", str),
        }
        row = {
            "Address": "10 High St, London",
            "Postcode": "SW1V 2QQ",
        }
        bootstrap_from_row(row, sources)
        a = await sources["corrected_address"].attempt()
        assert a.succeeded
        # Address gets postcode appended
        assert "SW1V 2QQ" in a.value_or_none()

    @pytest.mark.asyncio
    async def test_pushes_user_entered_address_with_outcode_replace(self):
        """When address ends with outcode (e.g. 'UB2'), user_entered gets full postcode."""
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "user_entered_address": UserInputNode[str]("user_addr", str),
            "rightmove_address": UserInputNode[str]("rm_addr", str),
        }
        row = {
            "Address": "31 Isambard Road, Southall, UB2",
            "Postcode": "UB2 4GN",
        }
        bootstrap_from_row(row, sources)
        a = await sources["user_entered_address"].attempt()
        assert a.succeeded
        assert a.value_or_none() == "31 Isambard Road, Southall, UB2 4GN"
        assert "UB2, UB2 4GN" not in a.value_or_none()  # no duplication

    @pytest.mark.asyncio
    async def test_skips_user_entered_when_address_unchanged(self):
        """When address already has full postcode, user_entered_address is not pushed."""
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "user_entered_address": UserInputNode[str]("user_addr2", str),
        }
        row = {
            "Address": "10 High St, London SW1V 2QQ",
            "Postcode": "SW1V 2QQ",
        }
        bootstrap_from_row(row, sources)
        a = await sources["user_entered_address"].attempt()
        assert not a.succeeded  # not pushed because upgraded == address

    @pytest.mark.asyncio
    async def test_all_sources_integration(self):
        from houses.nodes.bootstrap import bootstrap_from_row
        from houses.nodes.location import BestAddressNode, BestLocationNode

        precise = UserInputNode[GeoPoint]("precise_location", GeoPoint)
        corrected = UserInputNode[str]("corrected_address", str)
        user = UserInputNode[str]("user_entered_address", str)
        rightmove_addr = UserInputNode[str]("rightmove_address", str)
        rightmove_loc = UserInputNode[GeoPoint]("rightmove_location", GeoPoint)

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
            "user_entered_address": user,
        }
        bootstrap_from_row(row, sources)

        best_loc = BestLocationNode(
            "best_loc",
            precise_location=precise,
            rightmove_location=rightmove_loc,
            best_address=BestAddressNode(
                "best_addr",
                user_entered_address=user,
                corrected_address=corrected,
                rightmove_address=rightmove_addr,
            ),
        )
        a = await best_loc.attempt()
        assert a.succeeded
        assert a.value_or_none() == GeoPoint(51.6, -0.2)

class TestUpgradeAddress:
    def test_replaces_outcode_with_full_postcode(self):
        from houses.nodes.bootstrap import _upgrade_address

        result = _upgrade_address("31 Isambard Road, Southall, UB2", "UB2 4GN")
        assert result == "31 Isambard Road, Southall, UB2 4GN"

    def test_no_change_when_address_has_full_postcode(self):
        from houses.nodes.bootstrap import _upgrade_address

        result = _upgrade_address("31 Isambard Road, Southall, UB2 4GN", "UB2 4GN")
        assert result == "31 Isambard Road, Southall, UB2 4GN"

    def test_appends_when_no_outcode(self):
        from houses.nodes.bootstrap import _upgrade_address

        result = _upgrade_address("10 High St", "SW1V 2QQ")
        assert result == "10 High St, SW1V 2QQ"

    def test_empty_postcode_returns_original(self):
        from houses.nodes.bootstrap import _upgrade_address

        result = _upgrade_address("10 High St", "")
        assert result == "10 High St"

    def test_empty_address_returns_empty(self):
        from houses.nodes.bootstrap import _upgrade_address

        result = _upgrade_address("", "SW1V 2QQ")
        assert result == ""
