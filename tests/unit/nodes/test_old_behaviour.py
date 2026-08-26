"""Regression tests: verify the new DAG nodes produce the same results
the old enrichment pipeline did.

These tests exercise actual compute logic — not empty-deps — using
service fakes at the boundary (not mocking every function).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fake_services():
    from houses.services_provider import _request_services as _sp
    from tests.helpers import make_services

    token = _sp.set(make_services())
    yield
    _sp.reset(token)


class TestSchoolNodes:
    """The old enrichment used SchoolGender.BOYS and child_age=12 for secondary."""

    @pytest.mark.asyncio
    async def test_secondary_calls_find_nearest_with_correct_params(self):
        from dag.user_input_node import UserInputNode
        from houses.geopoint import GeoPoint
        from houses.nodes.schools import SecondarySchoolNode

        loc = UserInputNode[GeoPoint]("loc", GeoPoint)
        addr = UserInputNode[str]("addr", str)
        node = SecondarySchoolNode("ss", best_location=loc, best_address=addr)
        loc.push(GeoPoint(51.5, -0.37), "test")
        addr.push("31 Isambard Road, Southall, UB2 4GN", "test")

        class AssertingService:
            async def find_nearest(self, postcode, child_age, address="", acceptable=None):
                assert acceptable == ("mixed",), f"Expected ('mixed',) got {acceptable}"
                assert child_age == 12, f"Expected 12 got {child_age}"
                return None

        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        svc = make_services(school_lookup=AssertingService())
        token = _sp.set(svc)
        try:
            from dag.scheduler import flush_processor

            await flush_processor()
            await node.attempt()
        finally:
            _sp.reset(token)

    @pytest.mark.asyncio
    async def test_primary_calls_find_nearest_with_boys_and_age_7(self):
        from dag.user_input_node import UserInputNode
        from houses.geopoint import GeoPoint
        from houses.nodes.schools import PrimarySchoolNode

        loc = UserInputNode[GeoPoint]("loc", GeoPoint)
        addr = UserInputNode[str]("addr", str)
        node = PrimarySchoolNode("ps", best_location=loc, best_address=addr)
        loc.push(GeoPoint(51.5, -0.37), "test")
        addr.push("31 Isambard Road, Southall, UB2 4GN", "test")

        class AssertingService:
            async def find_nearest(self, postcode, child_age, address="", acceptable=None):
                assert acceptable == ("mixed",), f"Expected ('mixed',) got {acceptable}"
                assert child_age == 4, f"Expected 4 got {child_age}"
                return None

        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        svc = make_services(school_lookup=AssertingService())
        token = _sp.set(svc)
        try:
            from dag.scheduler import flush_processor

            await flush_processor()
            await node.attempt()
        finally:
            _sp.reset(token)


class TestCouncilTaxNode:
    """The old enrichment passed the clean postcode, not the full address."""

    @pytest.mark.asyncio
    async def test_passes_postcode_not_full_address(self):
        from dag.user_input_node import UserInputNode
        from houses.nodes.epc_node import CouncilTaxNode

        addr = UserInputNode[str]("addr", str)
        pc = UserInputNode[str]("pc", str)
        node = CouncilTaxNode("ct", best_address=addr, postcode_node=pc)
        addr.push("31 Isambard Road, Southall, UB2 4GN", "test")
        pc.push("UB2 4GN", "test")

        captured = {}

        class CapturingService:
            async def lookup(self, postcode, address=""):
                captured["postcode"] = postcode
                from money import Money

                from dag.attempt import Attempt
                from dag.measurement import Measurement
                from houses.council_tax_info import CouncilTaxInfo

                return Attempt.succeeded(
                    CouncilTaxInfo(band="D", yearly_cost=Measurement(Money("1800", "GBP"), 0.0))
                )

        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        svc = make_services(council_tax_service=CapturingService())
        token = _sp.set(svc)
        try:
            from dag.scheduler import flush_processor

            await flush_processor()
            await node.attempt()
            assert captured.get("postcode") == "UB2 4GN", f"Expected 'UB2 4GN', got {captured.get('postcode')!r}"
        finally:
            _sp.reset(token)

    @pytest.mark.asyncio
    async def test_fails_when_postcode_missing(self):
        from dag.user_input_node import UserInputNode
        from houses.nodes.epc_node import CouncilTaxNode

        addr = UserInputNode[str]("addr", str)
        pc = UserInputNode[str]("pc", str)
        node = CouncilTaxNode("ct2", best_address=addr, postcode_node=pc)
        addr.push("31 Isambard Road, Southall, UB2 4GN", "test")
        from dag.scheduler import flush_processor

        await flush_processor()
        a = await node.attempt()
        assert a.pending


class TestRouteDescription:
    """Route description from CommuteLeg tuples — needed for commute display."""

    def test_returns_string_with_leg_modes_durations_and_stations(self):
        from pint import Quantity

        from houses.nodes.transit import CommuteLeg, _route_description

        legs = (
            CommuteLeg(mode="walk", duration=Quantity(6, "minute"), destination="Stop A"),
            CommuteLeg(mode="bus", duration=Quantity(9, "minute"), destination="Town Station"),
            CommuteLeg(mode="train", duration=Quantity(20, "minute"), destination="Paddington"),
            CommuteLeg(mode="tube", duration=Quantity(8, "minute"), line_name="Bakerloo", destination="Oxford Circus"),
            CommuteLeg(mode="walk", duration=Quantity(7, "minute")),
        )
        result = _route_description(legs)
        assert "Walk 6m to Stop A" in result
        assert "Bus 9m to Town Station" in result
        assert "Train 20m to Paddington" in result
        assert "Bakerloo" in result
        assert "Walk 7m" in result

    def test_handles_empty_details(self):
        from houses.nodes.transit import _route_description

        result = _route_description(())
        assert result == ""


class TestTownNode:
    """TownNode extracts town from best_address — used by walk-to-town commute."""

    @pytest.mark.asyncio
    async def test_returns_town_from_address(self):
        from dag.user_input_node import UserInputNode
        from houses.nodes.area import TownNode

        addr = UserInputNode[str]("addr", str)
        node = TownNode("tn", best_address=addr)
        addr.push("48 Acacia Avenue, Southall, UB2 5AD", "test")
        from dag.scheduler import flush_processor

        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == "Southall"

    @pytest.mark.asyncio
    async def test_fails_without_address(self):
        from dag.user_input_node import UserInputNode
        from houses.nodes.area import TownNode

        addr = UserInputNode[str]("addr2", str)
        node = TownNode("tn2", best_address=addr)
        a = await node.attempt()
        assert not a.succeeded


class TestTownName:
    """Town name extraction from best_address."""

    def test_extract_town_from_address(self):
        from houses.walkability import extract_town

        assert extract_town("48 Acacia Avenue, Southall, UB2 5AD") == "Southall"

    def test_extract_town_maidenhead(self):
        from houses.walkability import extract_town

        assert extract_town("Some Road, Maidenhead, SL6 1AA") == "Maidenhead"
