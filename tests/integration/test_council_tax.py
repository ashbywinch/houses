"""Integration tests for council tax — uses committed API cache."""

from __future__ import annotations

from collections import namedtuple
from unittest.mock import AsyncMock, patch

import pytest
from money import Money

from dag.measurement import Measurement
from houses.council_tax import lookup_council_tax

MockBand = namedtuple("MockBand", ["band", "address", "postcode", "local_authority", "local_authority_url"])
MockPage = namedtuple("MockPage", ["rows"])


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
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(return_value=_make_page([]))
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result.impossible

    @pytest.mark.asyncio
    async def test_only_deleted_returns_impossible(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(return_value=_make_page(_make_bands([("DELETED", "Some Address")])))
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result.impossible

    @pytest.mark.asyncio
    async def test_no_match_returns_impossible(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(
                return_value=_make_page(_make_bands([("C", "123 OTHER STREET, NEWBURY, RG14 1AA")]))
            )
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result.impossible

    @pytest.mark.asyncio
    async def test_match_returns_band(self):
        with (
            patch("uk_property_apis.voa.VOAClient") as mock_voa,
            patch("houses.council_tax._lookup_yearly_cost", return_value=Money("1500", "GBP")),
        ):
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(
                return_value=_make_page(
                    _make_bands(
                        [
                            ("B", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA"),
                            ("C", "95 NORTHBROOK STREET, NEWBURY, RG14 1AA"),
                        ],
                        la="West Berkshire",
                    )
                )
            )
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result.succeeded
            ct = result.value_or_none()
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
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(
                return_value=_make_page(
                    _make_bands(
                        [
                            ("F", "1 RUPERT AVENUE, HIGH WYCOMBE, HP12 3NL"),
                            ("E", "2 RUPERT AVE, HIGH WYCOMBE, HP12 3NL"),
                            ("E", "4 RUPERT AVE, HIGH WYCOMBE, HP12 3NL"),
                        ]
                    )
                )
            )
            result = await lookup_council_tax("HP12 3NL", "Rupert Avenue, High Wycombe")
            assert result.impossible, f"street-only address must not claim a band, got {result.value_or_none()!r}"

    @pytest.mark.asyncio
    async def test_building_name_does_not_claim_a_longer_named_property(self):
        """"The Old Rectory" must not match the VOA row "THE OLD RECTORY
        COTTAGE" — the name has to be followed by a comma or the end of
        the address, or a partial name claims a different property's
        band (the same wrong-band class as the street-only bug)."""
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(
                return_value=_make_page(
                    _make_bands(
                        [
                            ("F", "THE OLD RECTORY COTTAGE, HIGH WYCOMBE, HP12 3NL"),
                            ("E", "2 RUPERT AVE, HIGH WYCOMBE, HP12 3NL"),
                        ],
                        la="Wycombe",
                    )
                )
            )
            result = await lookup_council_tax("HP12 3NL", "The Old Rectory, High Wycombe")
            assert result.impossible, f"partial building name must not claim a band, got {result.value_or_none()!r}"

    @pytest.mark.asyncio
    async def test_building_name_without_number_still_matches(self):
        """A genuine no-house-number address (a named building) must still
        resolve — the start-of-address rule must not break named
        properties like "The Old Rectory"."""
        with (
            patch("uk_property_apis.voa.VOAClient") as mock_voa,
            patch("houses.council_tax._lookup_yearly_cost", return_value=Money("1600", "GBP")),
        ):
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(
                return_value=_make_page(
                    _make_bands(
                        [
                            ("E", "THE OLD RECTORY, HIGH WYCOMBE, HP12 3NL"),
                            ("F", "1 RUPERT AVENUE, HIGH WYCOMBE, HP12 3NL"),
                        ],
                        la="Wycombe",
                    )
                )
            )
            result = await lookup_council_tax("HP12 3NL", "The Old Rectory, High Wycombe")
            assert result.succeeded
            assert result.value_or_none().band == "E"

    @pytest.mark.asyncio
    async def test_match_among_deleted_and_active(self):
        with (
            patch("uk_property_apis.voa.VOAClient") as mock_voa,
            patch("houses.council_tax._lookup_yearly_cost", return_value=Money("1800", "GBP")),
        ):
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(
                return_value=_make_page(
                    _make_bands(
                        [
                            ("DELETED", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA"),
                            ("D", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA"),
                        ],
                        la="West Berkshire",
                    )
                )
            )
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result.succeeded
            assert result.value_or_none().band == "D"

    @pytest.mark.asyncio
    async def test_match_partial_address(self):
        with (
            patch("uk_property_apis.voa.VOAClient") as mock_voa,
            patch("houses.council_tax._lookup_yearly_cost", return_value=None),
        ):
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(
                return_value=_make_page(
                    _make_bands(
                        [
                            ("H", "FLAT 2ND FLR 10 DOWNING STREET, LONDON, SW1A 2AA"),
                            ("H", "PRIME MINISTERS RESIDENCE 11-12 DOWNING STREET, LONDON, SW1A 2AA"),
                        ],
                        la="Westminster",
                    )
                )
            )
            result = await lookup_council_tax("SW1A 2AA", "10 Downing Street, London, SW1A 2AA")
            assert result.succeeded
            assert result.value_or_none().band == "H"

    @pytest.mark.asyncio
    async def test_no_local_authority_still_returns_band(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            bands = _make_bands([("B", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA")], la=None)
            instance.fetch_page = AsyncMock(return_value=_make_page(bands))
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result.succeeded
            ct = result.value_or_none()
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
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(side_effect=ConnectionError("VOA down"))
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result.impossible

    @pytest.mark.asyncio
    async def test_scottish_postcode_returns_impossible(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(return_value=_make_page([]))
            result = await lookup_council_tax("EH1 1AA", "1 Princes Street, Edinburgh, EH1 1AA")
            assert result.impossible

    @pytest.mark.asyncio
    async def test_welsh_band_i(self):
        with (
            patch("uk_property_apis.voa.VOAClient") as mock_voa,
            patch("houses.council_tax._lookup_yearly_cost", return_value=None),
        ):
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(
                return_value=_make_page(_make_bands([("I", "SOME ADDRESS, CARDIFF, CF10 1AA")], la="Cardiff"))
            )
            result = await lookup_council_tax("CF10 1AA", "Some Address, Cardiff, CF10 1AA")
            assert result.succeeded
            assert result.value_or_none().band == "I"

    @pytest.mark.asyncio
    async def test_no_building_identifier_returns_impossible(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(
                return_value=_make_page(_make_bands([("D", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA")]))
            )
            result = await lookup_council_tax("RG14 1AA", ", RG14 1AA")
            assert result.impossible
            assert result.error == "could not extract building identifier"

    @pytest.mark.asyncio
    async def test_ambiguous_street_name_returns_impossible(self):
        """Street-only address matching multiple VOA results must return impossible."""
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(
                return_value=_make_page(
                    _make_bands(
                        [
                            ("D", "1 PADDOCK HEIGHTS, TWYFORD, RG10"),
                            ("E", "2 PADDOCK HEIGHTS, TWYFORD, RG10"),
                        ],
                        la="Wokingham",
                    )
                )
            )
            result = await lookup_council_tax("RG10 0AP", "Paddock Heights, Twyford, RG10")
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
        with patch("houses.council_tax._load_rates") as mock_rates:
            mock_rates.return_value = {"woking": 2598.0}
            with patch("httpx.Client") as mock_client:
                instance = mock_client.return_value.__enter__.return_value
                resp = instance.get.return_value
                resp.status_code = 200
                resp.json.return_value = {"band_d_rate": None}
                from houses.council_tax import _lookup_yearly_cost

                result = _lookup_yearly_cost("F", "Woking")
                assert result == Money("3752.67", "GBP")

    def test_civaccount_success_used_first(self):
        with patch("houses.council_tax._load_rates") as mock_rates:
            mock_rates.return_value = {"woking": 999.0}
            with patch("httpx.Client") as mock_client:
                instance = mock_client.return_value.__enter__.return_value
                resp = instance.get.return_value
                resp.status_code = 200
                resp.json.return_value = {"band_d_rate": 500.0}
                from houses.council_tax import _lookup_yearly_cost

                result = _lookup_yearly_cost("D", "Woking")
                assert result == Money("500", "GBP")

    def test_prefix_match_fallback(self):
        with patch("houses.council_tax._load_rates") as mock_rates:
            mock_rates.return_value = {"woking": 2598.0}
            with patch("httpx.Client") as mock_client:
                instance = mock_client.return_value.__enter__.return_value
                resp = instance.get.return_value
                resp.status_code = 200
                resp.json.return_value = {"band_d_rate": None}
                from houses.council_tax import _lookup_yearly_cost

                result = _lookup_yearly_cost("F", "Woking")
                assert result == Money("3752.67", "GBP")

    def test_unknown_authority_returns_none(self):
        with patch("houses.council_tax._load_rates") as mock_rates:
            mock_rates.return_value = {}
            with patch("httpx.Client") as mock_client:
                instance = mock_client.return_value.__enter__.return_value
                resp = instance.get.return_value
                resp.status_code = 404
                from houses.council_tax import _lookup_yearly_cost

                result = _lookup_yearly_cost("D", "Nonexistent Council")
                assert result is None

    def test_london_borough_falls_back_to_csv(self):
        with patch("houses.council_tax._load_rates") as mock_rates:
            mock_rates.return_value = {"london boroughs (excluding gla)": 1559.0}
            with patch("httpx.Client") as mock_client:
                instance = mock_client.return_value.__enter__.return_value
                resp = instance.get.return_value
                resp.status_code = 404
                from houses.council_tax import _lookup_yearly_cost

                result = _lookup_yearly_cost("E", "Ealing")
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

        addr = UserInputNode("addr", str)
        addr.push("Paddock Heights, Twyford, RG10", "test")
        postcode = UserInputNode("pc", str)
        postcode.push("RG10 0AP", "test")

        node = CouncilTaxNode("ct/council_tax", best_address=addr, postcode_node=postcode)

        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(
                return_value=_make_page(
                    _make_bands(
                        [
                            ("D", "1 PADDOCK HEIGHTS, TWYFORD, RG10 0AP"),
                            ("E", "2 PADDOCK HEIGHTS, TWYFORD, RG10 0AP"),
                        ],
                        la="Wokingham",
                    )
                )
            )
            await flush_processor()
            await flush_processor()
            j = await node.to_json()

        assert j["status"] == "succeeded"
        assert j["value"]["band"] == "?"
        assert j["value"]["yearly_cost"]["value"]["amount"] == "1200.00"
        assert j["value"]["yearly_cost"]["stddev"] == 50.0
        prov = j["provenance"]
        assert prov.get("status", "") != "impossible"
        assert "estimated" in (prov.get("description") or "")
        assert prov["sourceType"] == "api"
        assert prov["label"]
