"""Integration tests for council tax — uses committed API cache."""

from __future__ import annotations

from collections import namedtuple
from typing import override
from unittest.mock import patch

import pytest
from money import Money

from dag.measurement import Measurement
from houses.council_tax import lookup_council_tax

MockBand = namedtuple("MockBand", ["band", "address", "postcode", "local_authority", "local_authority_url"])
MockPage = namedtuple("MockPage", ["rows"])


class FakeVoaClient:
    """Async-context-manager VOA client stand-in with a canned page.

    Injected via ``lookup_council_tax(voa_client_factory=...)`` so tests
    never patch ``uk_property_apis.voa.VOAClient``.
    """

    def __init__(self, page):
        self._page = page

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def fetch_page(self, postcode: str, page: int = 0):
        return self._page


class _RaisingVoaClient(FakeVoaClient):
    """VOA client whose fetch_page raises — tests the graceful path."""

    @override
    async def fetch_page(self, postcode: str, page: int = 0):
        raise ConnectionError("VOA down")


class FakeCivAccountResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeCivAccountClient:
    """Sync context manager returning a canned CivAccount response.

    Injected via ``_lookup_yearly_cost(client_factory=...)`` so tests
    never patch ``httpx.Client``.
    """

    def __init__(self, status_code, payload):
        self._status_code = status_code
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url):
        return FakeCivAccountResponse(self._status_code, self._payload)


def _make_bands(bands_and_addresses, la="Test Council"):
    results = []
    for band, addr in bands_and_addresses:
        import re

        pc = ""
        m = re.search(r"[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}", addr, re.IGNORECASE)
        if m:
            pc = m.group(0)
        results.append(MockBand(band=band, address=addr, postcode=pc, local_authority=la, local_authority_url=""))
    return results


def _make_page(bands_or_bands):
    bands = bands_or_bands if isinstance(bands_or_bands, list) else []
    return MockPage(rows=bands)


class TestLookupCouncilTax:
    @pytest.mark.asyncio
    async def test_no_address_returns_impossible(self):
        result = await lookup_council_tax("RG14 1AA")
        assert result.impossible

    @pytest.mark.asyncio
    async def test_empty_results_returns_impossible(self):
        result = await lookup_council_tax(
            "RG14 1AA",
            "94A Northbrook Street, Newbury, RG14 1AA",
            voa_client_factory=lambda: FakeVoaClient(_make_page([])),
        )
        assert result.impossible

    @pytest.mark.asyncio
    async def test_only_deleted_returns_impossible(self):
        result = await lookup_council_tax(
            "RG14 1AA",
            "94A Northbrook Street, Newbury, RG14 1AA",
            voa_client_factory=lambda: FakeVoaClient(_make_page(_make_bands([("DELETED", "Some Address")]))),
        )
        assert result.impossible

    @pytest.mark.asyncio
    async def test_no_match_returns_impossible(self):
        result = await lookup_council_tax(
            "RG14 1AA",
            "94A Northbrook Street, Newbury, RG14 1AA",
            voa_client_factory=lambda: FakeVoaClient(
                _make_page(_make_bands([("C", "123 OTHER STREET, NEWBURY, RG14 1AA")]))
            ),
        )
        assert result.impossible

    @pytest.mark.asyncio
    async def test_match_returns_band(self):
        result = await lookup_council_tax(
            "RG14 1AA",
            "94A Northbrook Street, Newbury, RG14 1AA",
            voa_client_factory=lambda: FakeVoaClient(
                _make_page(
                    _make_bands(
                        [
                            ("B", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA"),
                            ("C", "95 NORTHBROOK STREET, NEWBURY, RG14 1AA"),
                        ],
                        la="West Berkshire",
                    )
                )
            ),
            rate_lookup=lambda band, la: Money("1500", "GBP"),
        )
        assert result.succeeded
        ct = result.value_or_none()
        assert ct is not None
        assert ct.band == "B"
        assert ct.yearly_cost == Measurement(Money("1500", "GBP"), 0.0)
        assert "west-berkshire" in ct.evidence_url

    @pytest.mark.asyncio
    async def test_street_only_address_never_matches_numbered_property(self):
        """Regression: house 90427107 showed Band F for a street-only
        address. "Rupert Avenue" was substring-matched to the VOA row
        "1 RUPERT AVENUE" (the one row that spells the street in full)
        and that house's band was presented as the property's band. A
        street-level address must never be matched to a numbered row —
        it cannot identify the property, so the lookup must fail and the
        DAG falls back to the estimated figure."""
        result = await lookup_council_tax(
            "HP12 3NL",
            "Rupert Avenue, High Wycombe",
            page_fetcher=lambda pc, page: _make_page(
                _make_bands(
                    [
                        ("F", "1 RUPERT AVENUE, HIGH WYCOMBE, HP12 3NL"),
                        ("E", "2 RUPERT AVE, HIGH WYCOMBE, HP12 3NL"),
                        ("E", "4 RUPERT AVE, HIGH WYCOMBE, HP12 3NL"),
                    ]
                )
            ),
        )
        assert result.impossible, f"street-only address must not claim a band, got {result.value_or_none()!r}"

    @pytest.mark.asyncio
    async def test_building_name_does_not_claim_a_longer_named_property(self):
        """"The Old Rectory" must not match the VOA row "THE OLD RECTORY
        COTTAGE" — the name has to be followed by a comma or the end of
        the address, or a partial name claims a different property's
        band (the same wrong-band class as the street-only bug)."""
        result = await lookup_council_tax(
            "HP12 3NL",
            "The Old Rectory, High Wycombe",
            page_fetcher=lambda pc, page: _make_page(
                _make_bands(
                    [
                        ("F", "THE OLD RECTORY COTTAGE, HIGH WYCOMBE, HP12 3NL"),
                        ("E", "2 RUPERT AVE, HIGH WYCOMBE, HP12 3NL"),
                    ],
                    la="Wycombe",
                )
            ),
        )
        assert result.impossible, f"partial building name must not claim a band, got {result.value_or_none()!r}"

    @pytest.mark.asyncio
    async def test_flat_number_does_not_claim_the_house_number(self):
        """A query for "5 High Street" must not match the row
        "FLAT 5, 15 HIGH STREET" — that flat is at 15, not 5. The
        number has to be followed by the street name to count."""
        result = await lookup_council_tax(
            "RG14 1AA",
            "5 High Street, Newbury, RG14 1AA",
            page_fetcher=lambda pc, page: _make_page(
                _make_bands(
                    [
                        ("C", "FLAT 5, 15 HIGH STREET, NEWBURY, RG14 1AA"),
                        ("D", "15 HIGH STREET, NEWBURY, RG14 1AA"),
                    ],
                    la="West Berkshire",
                )
            ),
        )
        assert result.impossible, f"flat number must not claim the house band, got {result.value_or_none()!r}"

    @pytest.mark.asyncio
    async def test_flat_number_still_matches_its_own_street(self):
        """But "10 Downing Street" legitimately matches the row
        "FLAT 2ND FLR 10 DOWNING STREET" — the flat IS at 10."""
        result = await lookup_council_tax(
            "SW1A 2AA",
            "10 Downing Street, London, SW1A 2AA",
            page_fetcher=lambda pc, page: _make_page(
                _make_bands(
                    [
                        ("H", "FLAT 2ND FLR 10 DOWNING STREET, LONDON, SW1A 2AA"),
                        ("H", "PRIME MINISTERS RESIDENCE 11-12 DOWNING STREET, LONDON, SW1A 2AA"),
                    ],
                    la="Westminster",
                )
            ),
        )
        assert result.succeeded
        info = result.value_or_none()
        assert info is not None
        assert info.band == "H"

    @pytest.mark.asyncio
    async def test_flat_inside_named_building_resolves(self):
        """"Flat 3, The Old Rectory" must resolve its own VOA row
        ("THE OLD RECTORY, FLAT 3, ...") — the query's unit descriptor
        is matched against the unit named in the row, not rejected."""
        result = await lookup_council_tax(
            "HP12 3NL",
            "Flat 3, The Old Rectory, High Wycombe",
            page_fetcher=lambda pc, page: _make_page(
                _make_bands(
                    [
                        ("E", "THE OLD RECTORY, FLAT 3, HIGH WYCOMBE, HP12 3NL"),
                        ("F", "THE OLD RECTORY, HIGH WYCOMBE, HP12 3NL"),
                    ],
                    la="Wycombe",
                )
            ),
        )
        assert result.succeeded, f"flat inside a named building must resolve, got {result.error!r}"
        info = result.value_or_none()
        assert info is not None
        assert info.band == "E"

    @pytest.mark.asyncio
    async def test_flat_at_numbered_building_resolves(self):
        """"Flat 3, 123 High Street" must resolve its own VOA row — the
        standard row format puts the unit FIRST: "FLAT 3, 123 HIGH
        STREET, MAIDENHEAD"."""
        result = await lookup_council_tax(
            "SL6 1AA",
            "Flat 3, 123 High Street, Maidenhead, SL6 1AA",
            page_fetcher=lambda pc, page: _make_page(
                _make_bands(
                    [
                        ("C", "FLAT 3, 123 HIGH STREET, MAIDENHEAD, SL6 1AA"),
                        ("D", "123 HIGH STREET, MAIDENHEAD, SL6 1AA"),
                    ],
                    la="Windsor and Maidenhead",
                )
            ),
        )
        assert result.succeeded, f"flat at a numbered building must resolve, got {result.error!r}"
        info = result.value_or_none()
        assert info is not None
        assert info.band == "C"

    @pytest.mark.asyncio
    async def test_building_name_without_number_still_matches(self):
        """A genuine no-house-number address (a named building) must still
        resolve — the start-of-address rule must not break named
        properties like "The Old Rectory"."""
        result = await lookup_council_tax(
            "HP12 3NL",
            "The Old Rectory, High Wycombe",
            page_fetcher=lambda pc, page: _make_page(
                _make_bands(
                    [
                        ("E", "THE OLD RECTORY, HIGH WYCOMBE, HP12 3NL"),
                        ("F", "1 RUPERT AVENUE, HIGH WYCOMBE, HP12 3NL"),
                    ],
                    la="Wycombe",
                )
            ),
        )
        assert result.succeeded
        info = result.value_or_none()
        assert info is not None
        assert info.band == "E"

    @pytest.mark.asyncio
    async def test_match_among_deleted_and_active(self):
        result = await lookup_council_tax(
            "RG14 1AA",
            "94A Northbrook Street, Newbury, RG14 1AA",
            voa_client_factory=lambda: FakeVoaClient(
                _make_page(
                    _make_bands(
                        [
                            ("DELETED", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA"),
                            ("D", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA"),
                        ],
                        la="West Berkshire",
                    )
                )
            ),
            rate_lookup=lambda band, la: Money("1800", "GBP"),
        )
        assert result.succeeded
        ct = result.value_or_none()
        assert ct is not None
        assert ct.band == "D"

    @pytest.mark.asyncio
    async def test_match_partial_address(self):
        result = await lookup_council_tax(
            "SW1A 2AA",
            "10 Downing Street, London, SW1A 2AA",
            voa_client_factory=lambda: FakeVoaClient(
                _make_page(
                    _make_bands(
                        [
                            ("H", "FLAT 2ND FLR 10 DOWNING STREET, LONDON, SW1A 2AA"),
                            ("H", "PRIME MINISTERS RESIDENCE 11-12 DOWNING STREET, LONDON, SW1A 2AA"),
                        ],
                        la="Westminster",
                    )
                )
            ),
            rate_lookup=lambda band, la: None,
        )
        assert result.succeeded
        ct = result.value_or_none()
        assert ct is not None
        assert ct.band == "H"

    @pytest.mark.asyncio
    async def test_no_local_authority_still_returns_band(self):
        bands = _make_bands([("B", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA")], la=None)
        result = await lookup_council_tax(
            "RG14 1AA",
            "94A Northbrook Street, Newbury, RG14 1AA",
            voa_client_factory=lambda: FakeVoaClient(_make_page(bands)),
        )
        assert result.succeeded
        ct = result.value_or_none()
        assert ct is not None
        assert ct.band == "B"
        assert ct.yearly_cost is None
        assert ct.evidence_url == ""

    @pytest.mark.asyncio
    async def test_import_error_graceful(self):
        import sys

        with patch.dict(sys.modules, {"uk_property_apis": None, "uk_property_apis.voa": None}, clear=False):
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result.impossible

    @pytest.mark.asyncio
    async def test_voa_exception_graceful(self):
        result = await lookup_council_tax(
            "RG14 1AA",
            "94A Northbrook Street, Newbury, RG14 1AA",
            voa_client_factory=lambda: _RaisingVoaClient(None),
        )
        assert result.impossible

    @pytest.mark.asyncio
    async def test_scottish_postcode_returns_impossible(self):
        result = await lookup_council_tax(
            "EH1 1AA",
            "1 Princes Street, Edinburgh, EH1 1AA",
            voa_client_factory=lambda: FakeVoaClient(_make_page([])),
        )
        assert result.impossible

    @pytest.mark.asyncio
    async def test_welsh_band_i(self):
        result = await lookup_council_tax(
            "CF10 1AA",
            "Some Address, Cardiff, CF10 1AA",
            voa_client_factory=lambda: FakeVoaClient(
                _make_page(_make_bands([("I", "SOME ADDRESS, CARDIFF, CF10 1AA")], la="Cardiff"))
            ),
            rate_lookup=lambda band, la: None,
        )
        assert result.succeeded
        ct = result.value_or_none()
        assert ct is not None
        assert ct.band == "I"

    @pytest.mark.asyncio
    async def test_no_building_identifier_returns_impossible(self):
        result = await lookup_council_tax(
            "RG14 1AA",
            ", RG14 1AA",
            voa_client_factory=lambda: FakeVoaClient(
                _make_page(_make_bands([("D", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA")]))
            ),
        )
        assert result.impossible
        assert result.error == "could not extract building identifier"

    @pytest.mark.asyncio
    async def test_ambiguous_street_name_returns_impossible(self):
        """Street-only address matching multiple VOA results must return impossible."""
        result = await lookup_council_tax(
            "RG10 0AP",
            "Paddock Heights, Twyford, RG10",
            voa_client_factory=lambda: FakeVoaClient(
                _make_page(
                    _make_bands(
                        [
                            ("D", "1 PADDOCK HEIGHTS, TWYFORD, RG10"),
                            ("E", "2 PADDOCK HEIGHTS, TWYFORD, RG10"),
                        ],
                        la="Wokingham",
                    )
                )
            ),
        )
        assert result.impossible
        assert result.error == "address does not identify a single property"


class TestLookupYearlyCost:
    @pytest.mark.asyncio
    async def test_ratio_computation(self):
        from houses.council_tax import BAND_RATIOS

        assert BAND_RATIOS["F"] == 13 / 9
        result = round(307.0 * 13 / 9, 2)
        assert result == 443.44

    def test_civaccount_fallback_to_csv(self):
        from houses.council_tax import _lookup_yearly_cost

        result = _lookup_yearly_cost(
            "F",
            "Woking",
            load_rates_fn=lambda: {"woking": 2598.0},
            client_factory=lambda **kw: FakeCivAccountClient(200, {"band_d_rate": None}),
        )
        assert result == Money("3752.67", "GBP")

    def test_civaccount_success_used_first(self):
        from houses.council_tax import _lookup_yearly_cost

        result = _lookup_yearly_cost(
            "D",
            "Woking",
            load_rates_fn=lambda: {"woking": 999.0},
            client_factory=lambda **kw: FakeCivAccountClient(200, {"band_d_rate": 500.0}),
        )
        assert result == Money("500", "GBP")

    def test_prefix_match_fallback(self):
        from houses.council_tax import _lookup_yearly_cost

        result = _lookup_yearly_cost(
            "F",
            "Woking",
            load_rates_fn=lambda: {"woking": 2598.0},
            client_factory=lambda **kw: FakeCivAccountClient(200, {"band_d_rate": None}),
        )
        assert result == Money("3752.67", "GBP")

    def test_unknown_authority_returns_none(self):
        from houses.council_tax import _lookup_yearly_cost

        result = _lookup_yearly_cost(
            "D",
            "Nonexistent Council",
            load_rates_fn=lambda: {},
            client_factory=lambda **kw: FakeCivAccountClient(404, {}),
        )
        assert result is None

    def test_london_borough_falls_back_to_csv(self):
        from houses.council_tax import _lookup_yearly_cost

        result = _lookup_yearly_cost(
            "E",
            "Ealing",
            load_rates_fn=lambda: {"london boroughs (excluding gla)": 1559.0},
            client_factory=lambda **kw: FakeCivAccountClient(404, {}),
        )
        assert result is not None
        assert result == Money("1905.44", "GBP")


class TestCouncilTaxNodeProvenance:
    """The node path the frontend consumes: to_json() → provenance dict.

    Part A: a failed lookup is no longer "impossible" — the node returns
    a Band D estimate with a spread, and provenance notes the estimation
    so the UI can show the one-step reason ("≈").
    """

    @pytest.mark.asyncio
    async def test_failed_lookup_falls_back_to_estimate_in_provenance(self):
        from dag.scheduler import flush_processor
        from dag.user_input_node import UserInputNode
        from houses.nodes.epc_node import CouncilTaxNode
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        class CannedCouncilTaxService:
            """Real ``lookup_council_tax`` against a canned VOA page."""

            async def lookup(self, postcode, address=""):
                return await lookup_council_tax(
                    postcode,
                    address,
                    voa_client_factory=lambda: FakeVoaClient(
                        _make_page(
                            _make_bands(
                                [
                                    ("D", "1 PADDOCK HEIGHTS, TWYFORD, RG10 0AP"),
                                    ("E", "2 PADDOCK HEIGHTS, TWYFORD, RG10 0AP"),
                                ],
                                la="Wokingham",
                            )
                        )
                    ),
                )

        token = _sp.set(make_services(council_tax_service=CannedCouncilTaxService()))
        try:
            addr = UserInputNode("addr", str)
            addr.push("Paddock Heights, Twyford, RG10", "test")
            postcode = UserInputNode("pc", str)
            postcode.push("RG10 0AP", "test")

            node = CouncilTaxNode("ct/council_tax", best_address=addr, postcode_node=postcode)
            await flush_processor()
            await flush_processor()
            j = await node.to_json()
        finally:
            _sp.reset(token)

        assert j["status"] == "succeeded"
        assert j["value"]["band"] == "?"
        assert j["value"]["yearly_cost"]["value"]["amount"] == "1200.00"
        assert j["value"]["yearly_cost"]["stddev"] == 50.0
        prov = j["provenance"]
        assert prov.get("status", "") != "impossible"
        assert "estimated" in (prov.get("description") or "")
        assert prov["sourceType"] == "api"
        assert prov["label"]
