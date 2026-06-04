"""Tests for council tax band lookup via VOA scraper + CivAccount."""

from __future__ import annotations

from collections import namedtuple
from unittest.mock import AsyncMock, patch

import pytest

from houses.council_tax import _extract_building, _normalise, lookup_council_tax
from houses.models import CouncilTaxInfo

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


class TestExtractBuilding:
    def test_street_number(self):
        result = _extract_building("94A Northbrook Street, Newbury, RG14 1AA")
        assert result == {"postcode": "RG14 1AA", "building_number": "94A"}

    def test_simple_number(self):
        result = _extract_building("10 Downing Street, London, SW1A 2AA")
        assert result == {"postcode": "SW1A 2AA", "building_number": "10"}

    def test_named_building(self):
        result = _extract_building("Buckingham Palace, London, SW1A 1AA")
        assert result == {"postcode": "SW1A 1AA", "building_name": "Buckingham Palace"}

    def test_flat_format(self):
        result = _extract_building("Flat 3, 123 High Street, Maidenhead, SL6 1AA")
        assert result == {"postcode": "SL6 1AA", "building_name": "Flat 3"}

    def test_no_postcode_in_address(self):
        result = _extract_building("10 Downing Street, London")
        assert result == {"postcode": "", "building_number": "10"}

    def test_empty_address(self):
        result = _extract_building("")
        assert result == {"postcode": "", "building_name": ""}


class TestNormalise:
    def test_uppercases(self):
        assert _normalise("abc123") == "ABC123"

    def test_strips_punctuation(self):
        assert _normalise("94A, Flat!") == "94A FLAT"

    def test_strips_whitespace(self):
        assert _normalise("  hello  ") == "HELLO"

    def test_removes_parentheses(self):
        assert _normalise("Flat (2nd Floor)") == "FLAT 2ND FLOOR"

    def test_empty_string(self):
        assert _normalise("") == ""


class TestLoadRates:
    """_load_rates loads the CSV and caches it."""

    def test_loads_woking_rate(self):
        from houses.council_tax import _load_rates

        rates = _load_rates()
        assert "woking" in rates, "Woking should be in the rates CSV"
        assert rates["woking"] == 2598.0
        assert rates["sheffield"] == 2510.0
        # Most rates should be over £1,000 (total area Band D)
        below_1000 = sum(1 for v in rates.values() if v < 1000)
        assert below_1000 < 10, f"{below_1000} authorities have rates under £1,000"

    def test_contains_billing_authorities(self):
        from houses.council_tax import _load_rates

        rates = _load_rates()
        assert len(rates) > 100, "Should have 100+ billing authorities"
        assert all(isinstance(v, float) for v in rates.values())


class TestLookupYearlyCost:
    """_lookup_yearly_cost with CivAccount mocked."""

    @pytest.mark.asyncio
    async def test_ratio_computation(self):
        """Band F with rate 307 → 307 * 13/9 = 443.44."""
        from houses.council_tax import BAND_RATIOS

        assert BAND_RATIOS["F"] == 13 / 9
        result = round(307.0 * 13 / 9, 2)
        assert result == 443.44

    def test_civaccount_fallback_to_csv(self):
        """When CivAccount returns no rate, falls back to CSV."""
        with patch("houses.council_tax._load_rates") as mock_rates:
            mock_rates.return_value = {"woking": 2598.0}
            with patch("httpx.Client") as mock_client:
                instance = mock_client.return_value.__enter__.return_value
                resp = instance.get.return_value
                resp.status_code = 200
                resp.json.return_value = {"band_d_rate": None}

                from houses.council_tax import _lookup_yearly_cost

                result = _lookup_yearly_cost("F", "Woking")
                assert result == 3752.67  # 2598 * 13/9

    def test_civaccount_success_used_first(self):
        """CivAccount rate takes priority over CSV."""
        with patch("houses.council_tax._load_rates") as mock_rates:
            mock_rates.return_value = {"woking": 999.0}
            with patch("httpx.Client") as mock_client:
                instance = mock_client.return_value.__enter__.return_value
                resp = instance.get.return_value
                resp.status_code = 200
                resp.json.return_value = {"band_d_rate": 500.0}

                from houses.council_tax import _lookup_yearly_cost

                result = _lookup_yearly_cost("D", "Woking")
                assert result == 500.0  # 500 * 9/9 = 500, not 999

    def test_prefix_match_fallback(self):
        """Authority name prefix matching works (e.g. 'Woking' matches CSV 'Woking')."""
        with patch("houses.council_tax._load_rates") as mock_rates:
            mock_rates.return_value = {"woking": 2598.0}
            with patch("httpx.Client") as mock_client:
                instance = mock_client.return_value.__enter__.return_value
                resp = instance.get.return_value
                resp.status_code = 200
                resp.json.return_value = {"band_d_rate": None}

                from houses.council_tax import _lookup_yearly_cost

                result = _lookup_yearly_cost("F", "Woking")
                assert result == 3752.67

    def test_unknown_authority_returns_none(self):
        """Authority not in CSV and not in CivAccount returns None."""
        with patch("houses.council_tax._load_rates") as mock_rates:
            mock_rates.return_value = {}
            with patch("httpx.Client") as mock_client:
                instance = mock_client.return_value.__enter__.return_value
                resp = instance.get.return_value
                resp.status_code = 404

                from houses.council_tax import _lookup_yearly_cost

                result = _lookup_yearly_cost("D", "Nonexistent Council")
                assert result is None


class TestLookupCouncilTax:

    @pytest.mark.asyncio
    async def test_no_address_returns_none(self):
        result = await lookup_council_tax("RG14 1AA")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_results_returns_none(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = mock_voa.return_value.__aenter__.return_value
            instance.fetch_page = AsyncMock(return_value=_make_page([]))
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result is None

    @pytest.mark.asyncio
    async def test_only_deleted_returns_none(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = mock_voa.return_value.__aenter__.return_value
            instance.fetch_page = AsyncMock(
                return_value=_make_page(_make_bands([("DELETED", "Some Address")]))
            )
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result is None

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = mock_voa.return_value.__aenter__.return_value
            instance.fetch_page = AsyncMock(
                return_value=_make_page(_make_bands([("C", "123 OTHER STREET, NEWBURY, RG14 1AA")]))
            )
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result is None

    @pytest.mark.asyncio
    async def test_match_returns_band(self):
        with (
            patch("uk_property_apis.voa.VOAClient") as mock_voa,
            patch("houses.council_tax._lookup_yearly_cost", return_value=1500.0),
        ):
            instance = mock_voa.return_value.__aenter__.return_value
            instance.fetch_page = AsyncMock(
                return_value=_make_page(_make_bands(
                    [("B", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA"),
                     ("C", "95 NORTHBROOK STREET, NEWBURY, RG14 1AA")],
                    la="West Berkshire",
                ))
            )
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert isinstance(result, CouncilTaxInfo)
            assert result.band == "B"
            assert result.yearly_cost == 1500.0
            assert "west-berkshire" in result.evidence_url

    @pytest.mark.asyncio
    async def test_match_among_deleted_and_active(self):
        with (
            patch("uk_property_apis.voa.VOAClient") as mock_voa,
            patch("houses.council_tax._lookup_yearly_cost", return_value=1800.0),
        ):
            instance = mock_voa.return_value.__aenter__.return_value
            instance.fetch_page = AsyncMock(
                return_value=_make_page(_make_bands(
                    [("DELETED", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA"),
                     ("D", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA")],
                    la="West Berkshire",
                ))
            )
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert isinstance(result, CouncilTaxInfo)
            assert result.band == "D"

    @pytest.mark.asyncio
    async def test_match_partial_address(self):
        with (
            patch("uk_property_apis.voa.VOAClient") as mock_voa,
            patch("houses.council_tax._lookup_yearly_cost", return_value=None),
        ):
            instance = mock_voa.return_value.__aenter__.return_value
            instance.fetch_page = AsyncMock(
                return_value=_make_page(_make_bands(
                    [("H", "FLAT 2ND FLR 10 DOWNING STREET, LONDON, SW1A 2AA"),
                     ("H", "PRIME MINISTERS RESIDENCE 11-12 DOWNING STREET, LONDON, SW1A 2AA")],
                    la="Westminster",
                ))
            )
            result = await lookup_council_tax("SW1A 2AA", "10 Downing Street, London, SW1A 2AA")
            assert isinstance(result, CouncilTaxInfo)
            assert result.band == "H"

    @pytest.mark.asyncio
    async def test_no_local_authority_still_returns_band(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = mock_voa.return_value.__aenter__.return_value
            instance.fetch_page = AsyncMock(
                return_value=_make_page(_make_bands(
                    [("B", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA")], la=None,
                ))
            )
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert isinstance(result, CouncilTaxInfo)
            assert result.band == "B"
            assert result.yearly_cost is None
            assert result.evidence_url == ""

    @pytest.mark.asyncio
    async def test_import_error_graceful(self):
        import sys
        with patch.dict(sys.modules, {"uk_property_apis": None, "uk_property_apis.voa": None}, clear=False):
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result is None

    @pytest.mark.asyncio
    async def test_voa_exception_graceful(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = mock_voa.return_value.__aenter__.return_value
            instance.fetch_page = AsyncMock(side_effect=ConnectionError("VOA down"))
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result is None

    @pytest.mark.asyncio
    async def test_scottish_postcode_returns_none(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = mock_voa.return_value.__aenter__.return_value
            instance.fetch_page = AsyncMock(return_value=_make_page([]))
            result = await lookup_council_tax("EH1 1AA", "1 Princes Street, Edinburgh, EH1 1AA")
            assert result is None

    @pytest.mark.asyncio
    async def test_welsh_band_i(self):
        with (
            patch("uk_property_apis.voa.VOAClient") as mock_voa,
            patch("houses.council_tax._lookup_yearly_cost", return_value=None),
        ):
            instance = mock_voa.return_value.__aenter__.return_value
            instance.fetch_page = AsyncMock(
                return_value=_make_page(_make_bands(
                    [("I", "SOME ADDRESS, CARDIFF, CF10 1AA")], la="Cardiff",
                ))
            )
            result = await lookup_council_tax("CF10 1AA", "Some Address, Cardiff, CF10 1AA")
            assert isinstance(result, CouncilTaxInfo)
            assert result.band == "I"

    @pytest.mark.asyncio
    async def test_no_building_identifier_returns_none(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = mock_voa.return_value.__aenter__.return_value
            instance.fetch_page = AsyncMock(
                return_value=_make_page(_make_bands([("D", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA")]))
            )
            result = await lookup_council_tax("RG14 1AA", ", RG14 1AA")
            assert result is None
