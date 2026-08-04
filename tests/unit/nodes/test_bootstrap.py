from __future__ import annotations

import pytest
from money import Money

from dag.scheduler import flush_processor
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
        await flush_processor()
        await flush_processor()
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
        await flush_processor()
        await flush_processor()
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
        await flush_processor()
        await flush_processor()
        a = await sources["rightmove_bedrooms"].attempt()
        assert a.value_or_none() == "3"

    @pytest.mark.asyncio
    async def test_pushes_price(self):
        from houses.nodes.bootstrap import bootstrap_from_row

        sources = {
            "rightmove_price": UserInputNode[Money]("rightmove_price", Money),
        }
        row = {"Price (£)": "450,000"}
        bootstrap_from_row(row, sources)
        await flush_processor()
        a = await sources["rightmove_price"].attempt()
        assert a.value_or_none() == Money("450000", "GBP")

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
        await flush_processor()
        await flush_processor()
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
        await flush_processor()
        await flush_processor()
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
        await flush_processor()
        await flush_processor()
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
        await flush_processor()
        await flush_processor()
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
        await flush_processor()
        await flush_processor()
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
        await flush_processor()
        await flush_processor()
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

        # Create derived nodes before bootstrap so changed signals enqueue them
        best_addr = BestAddressNode(
            "best_addr",
            user_entered_address=user,
            corrected_address=corrected,
            rightmove_address=rightmove_addr,
        )
        best_loc = BestLocationNode(
            "best_loc",
            precise_location=precise,
            rightmove_location=rightmove_loc,
            best_address=best_addr,
        )

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
        await flush_processor()
        await flush_processor()

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

class TestSeedInputDefaults:
    """A pending input node with no producer permanently blocks every
    downstream refresh (the DAG waits for pending deps).  The load paths
    must materialise input defaults so the settings cascade can always
    propagate — an empty sheet "Status" cell must not freeze the money
    chain forever."""

    def _property(self, rid: str):
        from houses.nodes.property import PropertyNodes

        return PropertyNodes(rid)

    def test_defaults_pending_inputs(self):
        from money import Money

        from houses.nodes.bootstrap import _seed_input_defaults

        prop = self._property("99900001")  # nothing pushed — all pending
        _seed_input_defaults(prop)
        assert prop.comment_status.latest_attempt().value_or_none() == ""
        assert prop.works_estimates.latest_attempt().value_or_none() == {}
        assert prop.rental_income.latest_attempt().value_or_none() == Money("0", "GBP")

    def test_defaults_do_not_overwrite_user_values(self):
        from houses.nodes.bootstrap import _seed_input_defaults

        prop = self._property("99900002")
        prop.comment_status.push("Current", "user")
        _seed_input_defaults(prop)
        assert prop.comment_status.latest_attempt().value_or_none() == "Current"

    def test_settings_cascade_propagates_when_comment_status_was_never_seeded(self):
        """Production shape: comment_status has no DB row (empty sheet
        cell) — with the fix, the load-path defaults unblock the cascade
        and a settings change still flows to mortgage_required."""
        from fastapi.testclient import TestClient

        from houses.model.domain import Person
        from houses.nodes.bootstrap import _seed_input_defaults
        from houses.server import app
        from houses.web.auth import _make_session_cookie
        from tests.unit.nodes.test_api import _push_persons

        _push_persons(
            Person(name="Simon", has_car=True, home_sale_price=Money("550000", "GBP"),
                   outstanding_mortgage=Money("373000", "GBP")),
            Person(name="Ashby", has_car=True, cash_contribution=Money("300000", "GBP")),
        )

        rid = "99900003"
        prop = self._property(rid)
        prop.rightmove_price.push(Money("500000", "GBP"), "test")
        prop.rightmove_address.push("1 Test St", "test")
        prop.rightmove_bedrooms.push("3", "test")
        prop.rightmove_location.push(GeoPoint(51.5, -0.1), "test")
        prop.corrected_address.push("1 Test St, SW1V 2QQ", "test")
        prop.precise_location.push(GeoPoint(51.5, -0.1), "test")
        prop.postcode.push("SW1V 2QQ", "test")
        prop.user_entered_address.push("1 Test St, SW1V 2QQ", "test")
        # NOTE: comment_status deliberately left pending (production shape)
        from houses.property_registry import register_property

        _seed_input_defaults(prop)
        register_property(rid, prop)

        from tests.unit.conftest import flush_all

        flush_all()
        flush_all()

        client = TestClient(app)
        client.cookies.set(
            "session",
            _make_session_cookie(email="simon@example.com", name="Simon", picture="", is_superuser=True),
        )
        baseline = client.get(f"/api/properties/{rid}/detail").json()["affordability"]["mortgage_required"]

        resp = client.patch(
            "/api/settings/person/Ashby",
            json={"name": "Ashby", "has_car": True, "cash_contribution": {"amount": "310000", "currency": "GBP"}},
        )
        assert resp.status_code == 200
        flush_all()

        updated = client.get(f"/api/properties/{rid}/detail").json()["affordability"]["mortgage_required"]
        from tests.unit.nodes.test_api import _amount_of

        delta = _amount_of(updated) - _amount_of(baseline)
        assert delta == -10000, f"cascade blocked by pending input, mortgage moved {delta}"
