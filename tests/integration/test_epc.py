"""Tests for EPC lookup — uses httpx MockTransport for the HTTP layer."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient, MockTransport, Response

from houses.config import settings
from houses.epc import _match_cert, _should_lookup_epc, lookup_epc


@pytest.fixture(autouse=True)
def _ensure_token():
    """Ensure EPC token is set for tests that exercise the HTTP path."""
    saved = settings.epc_bearer_token
    settings.epc_bearer_token = "test-token"
    yield
    settings.epc_bearer_token = saved


# ── _should_lookup_epc tests ──


@pytest.mark.parametrize(
    "address,expected_proceed,expected_building_id",
    [
        # Numbered → proceed
        ("7 Sandy Close, Woking, GU22", True, "7"),
        ("1 Some Street, Town, PO1 1AA", True, "1"),
        ("22b Acacia Avenue, City, PO1 1AA", True, "22"),
        # Road name with space-separated suffix → skip
        ("Shoppenhangers Road, Maidenhead, SL6", False, ""),
        ("Nightingale Way, Denham Green, UB9 5JH", False, ""),
        ("Winston Drive, Cobham, KT11", False, ""),
        ("Sandy Close, Woking, GU22", False, ""),
        ("Carver Hill Road, High Wycombe, HP11", False, ""),
        # Named building → proceed
        ("Blue Dawes, Pangbourne on Thames, RG8 7AS", True, "Blue Dawes"),
        ("Ruskins, Goaters Road, Ascot, SL5 8HZ", True, "Ruskins"),
        ("Gorseway, Fleet, GU52", True, "Gorseway"),
        # Named building (2 parts, but clear name) → proceed
        ("Blue Dawes, Pangbourne on Thames", True, "Blue Dawes"),
        ("Just a name, Big City", True, "Just a name"),
        # Road suffix with space catches even 2-part addresses
        ("Some Road, Town", False, ""),
        ("Nightingale Way, Town", False, ""),
        ("Just a road, PO1", False, ""),
        ("Just a lane, somewhere", False, ""),
        # Gorseway-style: ends with 'way' as substring but not separate word → proceed
        ("Gorseway, Fleet, GU52", True, "Gorseway"),
        ("Just a road, PO1", False, ""),
        # Road suffix check works with space-separated words only
    ],
)
def test_should_lookup_epc(address, expected_proceed, expected_building_id):
    proceed, building_id = _should_lookup_epc(address)
    assert proceed == expected_proceed, f"Expected proceed={expected_proceed} for {address!r}"
    if expected_proceed:
        assert building_id == expected_building_id


# ── _match_cert tests ──


def test_match_cert_by_number():
    certs = [
        {"addressLine1": "9 Goaters Road", "registrationDate": "2024-01-01", "currentEnergyEfficiencyBand": "D"},
        {"addressLine1": "7 Goaters Road", "registrationDate": "2025-06-01", "currentEnergyEfficiencyBand": "C"},
    ]
    result = _match_cert(certs, "7")
    assert result.is_succeeded
    assert result.value_or("") == "C"


def test_match_cert_by_name():
    a1 = "addressLine1"
    a2 = "registrationDate"
    a3 = "currentEnergyEfficiencyBand"
    certs = [
        {a1: "ROSE GARDEN HOUSE PANGBOURNE HILL", a2: "2022-01-01", a3: "F"},
        {a1: "BLUE DAWES PANGBOURNE HILL", a2: "2024-06-15", a3: "D"},
        {a1: "MAY COTTAGE PANGBOURNE HILL", a2: "2023-03-10", a3: "E"},
    ]
    result = _match_cert(certs, "Blue Dawes")
    assert result.is_succeeded
    assert result.value_or("") == "D"


def test_match_cert_multiple_certs_same_building_returns_most_recent():
    """Multiple certs for the same building → most recent."""
    certs = [
        {"addressLine1": "7 Goaters Road", "registrationDate": "2024-01-01", "currentEnergyEfficiencyBand": "D"},
        {"addressLine1": "7 Goaters Road", "registrationDate": "2025-06-01", "currentEnergyEfficiencyBand": "C"},
    ]
    result = _match_cert(certs, "7")
    assert result.is_succeeded
    assert result.value_or("") == "C"


def test_match_cert_no_match_returns_impossible():
    """No certificate matches the given building_id."""
    certs = [
        {"addressLine1": "9 Goaters Road", "registrationDate": "2024-01-01", "currentEnergyEfficiencyBand": "D"},
    ]
    result = _match_cert(certs, "7")
    assert result.is_impossible
    assert result.reason == "no matching certificate for this address"


def test_match_cert_empty_certs_returns_impossible():
    """Empty certs list → impossible."""
    result = _match_cert([], "")
    assert result.is_impossible
    assert result.reason == "no certificates found"


def test_match_cert_empty_building_id_returns_most_recent():
    """With no building ID, returns the most recent certificate."""
    certs = [
        {"addressLine1": "9 Goaters Road", "registrationDate": "2024-01-01", "currentEnergyEfficiencyBand": "D"},
        {"addressLine1": "7 Goaters Road", "registrationDate": "2025-06-01", "currentEnergyEfficiencyBand": "C"},
    ]
    result = _match_cert(certs, "")
    assert result.is_succeeded
    assert result.value_or("") == "C"


def test_match_cert_ambiguous_different_addresses_returns_impossible():
    """Multiple different addresses matching the same building_id → ambiguous."""
    certs = [
        {"addressLine1": "Rose Cottage", "registrationDate": "2024-01-01", "currentEnergyEfficiencyBand": "D"},
        {"addressLine1": "Rose Garden House", "registrationDate": "2025-06-01", "currentEnergyEfficiencyBand": "C"},
    ]
    result = _match_cert(certs, "Rose")
    assert result.is_impossible
    assert result.reason == "address matched multiple properties"


# ── lookup_epc with address tests ──


@pytest.mark.asyncio
async def test_lookup_epc_with_road_name_skips():
    """Road-name address should return empty without calling the API."""

    def handler(request):
        raise AssertionError("Should not be called")

    original_init = AsyncClient.__init__

    def patched_init(self, **kwargs):
        kwargs["transport"] = MockTransport(handler)
        original_init(self, **kwargs)

    with patch.object(AsyncClient, "__init__", patched_init):
        band = await lookup_epc("UB9 5JH", "Nightingale Way, Denham Green, UB9 5JH")

    assert band == ""


@pytest.mark.asyncio
async def test_lookup_epc_with_named_building_matches(_mock_http_requests):
    """Named-building address should proceed and match by name."""
    _mock_http_requests.add_rule(
        lambda url: "get-energy-performance-data" in url,
        lambda request: Response(
            200,
            json={
                "data": [
                    {
                        "addressLine1": "BLUE DAWES PANGBOURNE HILL",
                        "registrationDate": "2024-06-15",
                        "currentEnergyEfficiencyBand": "D",
                    }
                ]
            },
        ),
    )
    band = await lookup_epc("RG8 7AS", "Blue Dawes, Pangbourne on Thames, RG8 7AS")

    assert band == "D"


# ── Existing tests ──


@pytest.mark.asyncio
async def test_returns_band_from_most_recent_certificate():
    """Should return the band from the most recent certificate by registration date."""

    def handler(request):
        assert "postcode=RG14" in str(request.url)
        return Response(
            200,
            json={
                "data": [
                    {"registrationDate": "2020-01-01", "currentEnergyEfficiencyBand": "D"},
                    {"registrationDate": "2024-06-15", "currentEnergyEfficiencyBand": "C"},
                    {"registrationDate": "2022-03-10", "currentEnergyEfficiencyBand": "B"},
                ]
            },
        )

    original_init = AsyncClient.__init__

    def patched_init(self, **kwargs):
        kwargs["transport"] = MockTransport(handler)
        original_init(self, **kwargs)

    with patch.object(AsyncClient, "__init__", patched_init):
        band = await lookup_epc("RG14 1AA")

    assert band == "C"  # Most recent (2024)


@pytest.mark.asyncio
async def test_no_certificates_returns_empty(_mock_http_requests):
    """No certificates should return empty string."""
    _mock_http_requests.add_rule(
        lambda url: "get-energy-performance-data" in str(url),
        lambda request: Response(200, json={"data": []}),
    )
    band = await lookup_epc("SL6")

    assert band == ""


@pytest.mark.asyncio
async def test_non_200_response_returns_empty(_mock_http_requests):
    """API returning non-200 should be handled gracefully."""
    _mock_http_requests.add_rule(
        lambda url: "get-energy-performance-data" in str(url),
        lambda request: Response(500),
    )
    band = await lookup_epc("RG14 1AA")

    assert band == ""


@pytest.mark.asyncio
async def test_no_token_returns_empty():
    """With no bearer token, returns empty without making any HTTP call."""

    def handler(request):
        raise AssertionError("Should not be called")

    settings.epc_bearer_token = ""
    original_init = AsyncClient.__init__

    def patched_init(self, **kwargs):
        kwargs["transport"] = MockTransport(handler)
        original_init(self, **kwargs)

    with patch.object(AsyncClient, "__init__", patched_init):
        band = await lookup_epc("RG14 1AA")

    assert band == ""
