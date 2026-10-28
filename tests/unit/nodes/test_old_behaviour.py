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
        from houses.nodes.nearest_school_node import NearestSchoolNode

        loc = UserInputNode[GeoPoint]("loc", GeoPoint)
        addr = UserInputNode[str]("addr", str)
        node = NearestSchoolNode("ss", child_age=12, stage="secondary", best_location=loc)
        loc.push(GeoPoint(51.5, -0.37), "test")
        addr.push("31 Example Road, Anytown, AB1 2CD", "test")

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
        from houses.nodes.nearest_school_node import NearestSchoolNode

        loc = UserInputNode[GeoPoint]("loc", GeoPoint)
        addr = UserInputNode[str]("addr", str)
        node = NearestSchoolNode("ps", child_age=4, stage="primary", best_location=loc)
        loc.push(GeoPoint(51.5, -0.37), "test")
        addr.push("31 Example Road, Anytown, AB1 2CD", "test")

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
        addr.push("31 Example Road, Anytown, AB1 2CD", "test")
        pc.push("AB1 2CD", "test")

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
            assert captured.get("postcode") == "AB1 2CD", f"Expected 'AB1 2CD', got {captured.get('postcode')!r}"
        finally:
            _sp.reset(token)

    @pytest.mark.asyncio
    async def test_fails_when_postcode_missing(self):
        from dag.user_input_node import UserInputNode
        from houses.nodes.epc_node import CouncilTaxNode

        addr = UserInputNode[str]("addr", str)
        pc = UserInputNode[str]("pc", str)
        node = CouncilTaxNode("ct2", best_address=addr, postcode_node=pc)
        addr.push("31 Example Road, Anytown, AB1 2CD", "test")
        from dag.scheduler import flush_processor

        await flush_processor()
        a = await node.attempt()
        assert a.pending


class TestTownNode:
    """TownNode extracts town from best_address — used by walk-to-town commute."""

    @pytest.mark.asyncio
    async def test_returns_town_from_address(self):
        from dag.user_input_node import UserInputNode
        from houses.nodes.town_node import TownNode

        addr = UserInputNode[str]("addr", str)
        node = TownNode("tn", best_address=addr)
        addr.push("48 Sample Avenue, Anytown, CD4 5EF", "test")
        from dag.scheduler import flush_processor

        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() == "Anytown"

    @pytest.mark.asyncio
    async def test_fails_without_address(self):
        from dag.user_input_node import UserInputNode
        from houses.nodes.town_node import TownNode

        addr = UserInputNode[str]("addr2", str)
        node = TownNode("tn2", best_address=addr)
        a = await node.attempt()
        assert not a.succeeded


class TestTownName:
    """Town name extraction from best_address."""

    def test_extract_town_from_address(self):
        from houses.walkability import extract_town

        assert extract_town("48 Sample Avenue, Anytown, CD4 5EF") == "Anytown"

    def test_extract_town_maidenhead(self):
        from houses.walkability import extract_town

        assert extract_town("Some Road, Maidenhead, SL6 1AA") == "Maidenhead"
