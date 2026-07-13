"""Verify the JSON output shape of PropertyNodes.

Uses the Services DI container with fake implementations so no
external APIs are called during tests.
"""
from __future__ import annotations

import pytest

from houses.geo import GeoPoint


@pytest.fixture(autouse=True)
def _fake_services(monkeypatch):
    """Set fake singleton services so no real API calls are made."""
    from houses.services_provider import _request_services as _sp
    from tests.helpers import make_services

    token = _sp.set(make_services())

    # Mock HTTP-calling enrichment functions not routed through Services
    from money import Money

    from dag.attempt import Attempt
    from houses.commute import Commute
    from houses.school import School
    from houses.school_gender import SchoolGender

    async def fake_get_commute(origin, dest, *, has_car, max_walk_minutes):
        return Attempt.succeeded(
            Commute(
                destination_label="Office",
                destination_postcode=dest,
                duration_minutes=32,
                daily_cost_gbp=Money("4.50", "GBP"),
            ),
        )

    import houses.nodes.transit as transit_mod
    monkeypatch.setattr(transit_mod, "get_commute", fake_get_commute)

    # Provide a fake school lookup so school nodes + George's commutes work
    from houses.services_provider import _request_services as _sp
    from tests.helpers import make_services
    svc = _sp.get()
    if svc:
        async def fake_find_nearest(*args, **kwargs):
            return School(
                urn="123", name="Test School", phase="primary",
                gender=SchoolGender.BOYS,
                type_of_establishment="community school",
                postcode="SW1V 2QQ", website="",
                ofsted_rating="Good", inspection_year="2022",
                coords=None, statutory_low_age=None,
                statutory_high_age=None,
            )
        svc.school_lookup = type("FakeSchoolService", (), {
            "find_nearest": fake_find_nearest,
        })()

    yield
    _sp.reset(token)


@pytest.fixture
def prop():
    from houses.nodes.property import PropertyNodes

    p = PropertyNodes("test_shape")
    p.rightmove_price.push("550000", "test")
    p.rightmove_address.push("31 Isambard Rd", "test")
    p.rightmove_bedrooms.push("3", "test")
    p.rightmove_location.push(GeoPoint(51.48, -0.35), "rightmove")
    p.corrected_address.push("31 Isambard Rd, SW1V 2QQ", "test")
    p.precise_location.push(GeoPoint(51.5, -0.37), "test")
    p.postcode.push("SW1V 2QQ", "test")
    p.user_entered_address.push("31 Isambard Rd, SW1V 2QQ", "test")
    return p


class TestSummaryShape:
    @pytest.mark.asyncio
    async def test_has_expected_keys(self, prop):
        s = await prop.to_json_summary()
        assert s["rid"] == "test_shape"
        assert "best_address" in s
        assert "best_location" in s
        assert "rightmove_price" in s
        assert "rightmove_bedrooms" in s
        assert "total_monthly_cost" in s
        assert "commutes" in s
        assert "schools" in s
        assert "walkability" in s

    @pytest.mark.asyncio
    async def test_every_value_is_wrapped(self, prop):
        s = await prop.to_json_summary()
        for key in ("best_address", "best_location", "rightmove_price",
                     "rightmove_bedrooms", "total_monthly_cost", "walkability"):
            assert "status" in s[key], f"{key} missing status"
            assert "value" in s[key], f"{key} missing value"
            assert "provenance" in s[key], f"{key} missing provenance"


class TestDetailShape:
    @pytest.mark.asyncio
    async def test_has_all_sections(self, prop):
        d = await prop.to_json_detail()
        assert d["rid"] == "test_shape"
        for section in ("location", "commutes", "schools", "affordability",
                         "area", "comments", "settings"):
            assert section in d, f"missing section: {section}"

    @pytest.mark.asyncio
    async def test_affordability_keys(self, prop):
        d = await prop.to_json_detail()
        af = d["affordability"]
        expected = ("council_tax", "monthly_mortgage", "monthly_sinking_fund",
                     "monthly_commute_cost", "total_monthly_housing_cost")
        for key in expected:
            assert key in af, f"missing affordability key: {key}"

    @pytest.mark.asyncio
    async def test_comments_keys(self, prop):
        d = await prop.to_json_detail()
        cm = d["comments"]
        expected = ("status", "status_reason", "group_notes", "ashby_comments",
                     "ashby_works_estimate", "design_needed", "planning_needed")
        for key in expected:
            assert key in cm, f"missing comments key: {key}"

    @pytest.mark.asyncio
    async def test_location_keys(self, prop):
        d = await prop.to_json_detail()
        loc = d["location"]
        expected = ("best_location", "geocode", "rightmove_location",
                     "precise_location")
        for key in expected:
            assert key in loc, f"missing location key: {key}"

    @pytest.mark.asyncio
    async def test_settings_keys(self, prop):
        d = await prop.to_json_detail()
        s = d["settings"]
        assert "persons" in s
        assert "financial" in s

    @pytest.mark.asyncio
    async def test_schools_keys(self, prop):
        d = await prop.to_json_detail()
        sc = d["schools"]
        assert "primary" in sc
        assert "secondary" in sc
        assert "school" in sc["primary"]
        assert "school" in sc["secondary"]

    @pytest.mark.asyncio
    async def test_monthly_sinking_is_monthly_not_yearly(self, prop):
        d = await prop.to_json_detail()
        sf = d["affordability"]["monthly_sinking_fund"]
        assert sf["status"] == "succeeded"
        assert sf["value"] < 1000, f"sinking fund {sf['value']} looks like yearly, not monthly"


class TestCommuteData:
    """Exercises the success path of the commute pipeline.

    The _fake_services fixture patches get_commute to return real data,
    so TransitNode and CommuteSelectorNode should produce populated
    attempts. If field extraction has mismatch with the old Commute
    domain class (e.g. daily_cost vs daily_cost_gbp), this test fails.
    """

    @pytest.mark.asyncio
    async def test_commute_data_has_duration_with_value_and_unit(self, prop):
        for key, selector in prop.commute_selectors.items():
            j = await selector.to_json()
            assert j["status"] == "succeeded", f"{key}: {j.get('error')}"
            dur = j["value"]["duration"]
            assert isinstance(dur["value"], (int, float)), f"{key}: duration.value not numeric"
            assert dur["unit"] == "minute", f"{key}: duration.unit not 'minute'"

    @pytest.mark.asyncio
    async def test_commute_data_has_daily_cost_with_amount_and_currency(self, prop):
        for key, selector in prop.commute_selectors.items():
            j = await selector.to_json()
            assert j["status"] == "succeeded", f"{key}: {j.get('error')}"
            cost = j["value"]["daily_cost"]
            assert isinstance(cost["amount"], (int, float)), f"{key}: cost.amount not numeric"
            assert cost["currency"] == "GBP", f"{key}: cost.currency not GBP"

    @pytest.mark.asyncio
    async def test_commute_data_has_label(self, prop):
        for key, selector in prop.commute_selectors.items():
            j = await selector.to_json()
            assert j["status"] == "succeeded", f"{key}: {j.get('error')}"
            val = j.get("value", {})
            assert isinstance(val, dict), f"{key}: value not dict: {type(val)}"
            assert "label" in val, f"{key}: keys={list(val.keys())}"
            # Label is the POI label from settings (Bracknell, Office, Dad, Primary School, …)
            assert val["label"], f"{key}: empty label"

    @pytest.mark.asyncio
    async def test_commute_duration_appears_in_summary(self, prop):
        """List page PropertyCard accesses c.commute.value.duration.value."""
        s = await prop.to_json_summary()
        for key, cd in s["commutes"].items():
            c = cd["commute"]
            assert c["status"] == "succeeded", f"{key}: {c.get('error')}"
            dur = c["value"]["duration"]
            assert isinstance(dur["value"], (int, float))
            assert dur["value"] > 0
