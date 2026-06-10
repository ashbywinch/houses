"""Integration tests for council tax — uses committed API cache."""

from __future__ import annotations

from collections import namedtuple
from unittest.mock import AsyncMock, patch

import pytest

from houses.council_tax import lookup_council_tax
from houses.property import CouncilTaxInfo

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
    async def test_no_address_returns_none(self):
        result = await lookup_council_tax("RG14 1AA")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_results_returns_none(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(return_value=_make_page([]))
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result is None

    @pytest.mark.asyncio
    async def test_only_deleted_returns_none(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(return_value=_make_page(_make_bands([("DELETED", "Some Address")])))
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result is None

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
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
            assert isinstance(result, CouncilTaxInfo)
            assert result.band == "D"

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
            assert isinstance(result, CouncilTaxInfo)
            assert result.band == "H"

    @pytest.mark.asyncio
    async def test_no_local_authority_still_returns_band(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(
                return_value=_make_page(_make_bands([("B", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA")], la=None))
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
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(side_effect=ConnectionError("VOA down"))
            result = await lookup_council_tax("RG14 1AA", "94A Northbrook Street, Newbury, RG14 1AA")
            assert result is None

    @pytest.mark.asyncio
    async def test_scottish_postcode_returns_none(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(return_value=_make_page([]))
            result = await lookup_council_tax("EH1 1AA", "1 Princes Street, Edinburgh, EH1 1AA")
            assert result is None

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
            assert isinstance(result, CouncilTaxInfo)
            assert result.band == "I"

    @pytest.mark.asyncio
    async def test_no_building_identifier_returns_none(self):
        with patch("uk_property_apis.voa.VOAClient") as mock_voa:
            instance = AsyncMock()
            mock_voa.return_value = instance
            instance.fetch_page = AsyncMock(
                return_value=_make_page(_make_bands([("D", "94A NORTHBROOK STREET, NEWBURY, RG14 1AA")]))
            )
            result = await lookup_council_tax("RG14 1AA", ", RG14 1AA")
            assert result is None


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
                assert result == 3752.67

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
                assert result == 500.0

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
                assert result == 3752.67

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
                assert result == 1905.44
