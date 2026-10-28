"""Startup bootstrap: the DB loader that builds every property's live node
graph, plus the address upgrade and input-default invariants its consumers
share."""

from __future__ import annotations

from money import Money

from dag.persistence import save_node_result
from houses.geopoint import GeoPoint


class TestLoadPropertyNodesFromDb:
    """The live startup path (houses/server.py::_seed_dag): every RID in
    node_results becomes a registered PropertyNodes instance.  The
    test-data pollution guard on the same loader has its own test in
    tests/unit/dag/test_property_rids.py."""

    def test_registers_one_property_per_rid_in_the_db(self):
        from houses.nodes.bootstrap import load_property_nodes_from_db
        from houses.property_registry import get_property

        rids = ("89306649", "89306650")
        for rid in rids:
            save_node_result(
                f"{rid}/rightmove_address",
                {"status": "succeeded", "value": "10 High St, SW1P 1AA"},
                created_at="2026-01-01T00:00:00+00:00",
                code_version="bootstrap-test",
            )

        assert load_property_nodes_from_db() == len(rids)

        for rid in rids:
            prop = get_property(rid)
            assert prop is not None, f"{rid} is in the DB but was not registered"
            assert prop.rightmove_address.latest_attempt().value_or_none() == "10 High St, SW1P 1AA", (
                "a loaded property must come up holding its persisted values"
            )

    def test_an_empty_db_loads_nothing(self):
        from houses.nodes.bootstrap import load_property_nodes_from_db
        from houses.property_registry import list_properties

        assert load_property_nodes_from_db() == 0
        assert list_properties() == []


class TestUpgradeAddress:
    def test_replaces_outcode_with_full_postcode(self):
        from houses.location import upgrade_address as _upgrade_address

        result = _upgrade_address("31 Example Road, Anytown, AB1", "AB1 2CD")
        assert result == "31 Example Road, Anytown, AB1 2CD"

    def test_no_change_when_address_has_full_postcode(self):
        from houses.location import upgrade_address as _upgrade_address

        result = _upgrade_address("31 Example Road, Anytown, AB1 2CD", "AB1 2CD")
        assert result == "31 Example Road, Anytown, AB1 2CD"

    def test_appends_when_no_outcode(self):
        from houses.location import upgrade_address as _upgrade_address

        result = _upgrade_address("10 High St", "SW1P 1AA")
        assert result == "10 High St, SW1P 1AA"

    def test_empty_postcode_returns_original(self):
        from houses.location import upgrade_address as _upgrade_address

        result = _upgrade_address("10 High St", "")
        assert result == "10 High St"

    def test_empty_address_returns_empty(self):
        from houses.location import upgrade_address as _upgrade_address

        result = _upgrade_address("", "SW1P 1AA")
        assert result == ""

    def test_case_insensitive_present_postcode_is_not_doubled(self):
        """The 'already present' check must be case-insensitive — a
        lowercase postcode in the address plus an uppercase one would
        otherwise append a duplicate (addresses arrive in mixed case)."""
        from houses.location import upgrade_address as _upgrade_address

        result = _upgrade_address("Penwood Lane, Sampleton, sl7 2ap", "SL7 2AP")
        assert result == "Penwood Lane, Sampleton, sl7 2ap"

    def test_repeated_upgrade_is_idempotent(self):
        """Upgrading an already-upgraded address must not double the
        postcode (re-enrich / re-report paths re-run the upgrade)."""
        from houses.location import upgrade_address as _upgrade_address

        once = _upgrade_address("31 Example Road, Anytown, AB1", "AB1 2CD")
        twice = _upgrade_address(once, "AB1 2CD")
        assert once == "31 Example Road, Anytown, AB1 2CD"
        assert twice == once

    def test_different_postcode_variant_is_not_doubled_onto_the_same_outcode(self):
        """A full postcode must never be appended after the outcode the
        address already carries in full form."""
        from houses.location import upgrade_address as _upgrade_address

        result = _upgrade_address("Winston Drive, Cobham, KT11", "KT11")
        assert result == "Winston Drive, Cobham, KT11"


class TestSeedInputDefaults:
    """A pending input node with no producer permanently blocks every
    downstream refresh (the DAG waits for pending deps).  PropertyNodes
    construction itself materialises empty-valid input defaults, so NO
    creation path — startup load, add flow, scrape apply — can leave one
    pending: the invariant lives in __init__."""

    def _property(self, rid: str):
        from houses.nodes.property_nodes import PropertyNodes

        return PropertyNodes(rid)

    def test_construction_materialises_pending_inputs(self):
        from money import Money

        prop = self._property("99900001")  # nothing pushed — all pending
        assert prop.comment_status.latest_attempt().value_or_none() == ""
        assert prop.comment_status_reason.latest_attempt().value_or_none() == ""
        assert prop.works_estimates.latest_attempt().value_or_none() == {}
        assert prop.rental_income.latest_attempt().value_or_none() == Money(amount="0", currency="GBP")

    def test_construction_does_not_overwrite_user_values(self):
        prop = self._property("99900002")
        prop.comment_status.push("Current", "user")
        assert prop.comment_status.latest_attempt().value_or_none() == "Current"

    def test_settings_cascade_propagates_when_comment_status_was_never_persisted(self):
        """Production shape: comment_status has no DB row (empty sheet
        cell) — construction materialises it, and a settings change still
        flows to mortgage_required."""
        from fastapi.testclient import TestClient

        from houses.model.domain import Person
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
        prop.corrected_address.push("1 Test St, SW1P 1AA", "test")
        prop.precise_location.push(GeoPoint(51.5, -0.1), "test")
        prop.user_entered_address.push("1 Test St, SW1P 1AA", "test")
        # comment_status deliberately left unpersisted (production shape)
        from houses.property_registry import register_property

        register_property(rid, prop)

        from tests.unit.conftest import flush_all

        flush_all()  # one drain cascades the whole wave (see coding-standards)

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
