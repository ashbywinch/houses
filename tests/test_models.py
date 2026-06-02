"""Tests for data models."""

from houses.models import (
    EnrichedProperty,
    PetrolCost,
    PropertyPayload,
    SchoolInfo,
    TransitInfo,
)


def test_property_payload() -> None:
    payload = PropertyPayload(
        url="https://www.rightmove.co.uk/properties/123",
        address="High Street, Some Town, RG14 1AA",
        bedrooms=3,
        price=650000.0,
    )
    assert payload.bedrooms == 3
    assert payload.address == "High Street, Some Town, RG14 1AA"
    assert payload.url.startswith("https://www.rightmove.co.uk/")


def test_property_payload_minimal() -> None:
    """Only url is required — address, bedrooms, price are optional."""
    payload = PropertyPayload(url="https://www.rightmove.co.uk/properties/123")
    assert payload.address == ""
    assert payload.bedrooms is None
    assert payload.price is None


def test_enriched_property_defaults() -> None:
    ep = EnrichedProperty(
        url="https://www.rightmove.co.uk/properties/123",
    )
    assert ep.address == ""
    assert ep.postcode == ""
    assert ep.bedrooms == 0
    assert ep.price == 0.0
    assert ep.simon_commute is None
    assert ep.lorena_commute is None
    assert ep.petrol is None
    assert ep.primary_school is None
    assert ep.secondary_school is None


def test_transit_info() -> None:
    t = TransitInfo(
        destination_label="Test",
        destination_postcode="EC3A 7LP",
        duration_minutes=30,
    )
    assert t.mode == "transit"


def test_school_info_defaults() -> None:
    s = SchoolInfo(name="Test School", type="primary")
    assert s.gender == "mixed"
    assert s.fee_paying is False


def test_petrol_cost_defaults() -> None:
    p = PetrolCost()
    assert p.destination == "Bracknell Office (RG12 8YA)"
    assert p.cost_gbp is None
