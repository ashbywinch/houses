"""Tests for data models."""

from houses.commute import Commute, CommuteBreakdown
from houses.property import CouncilTaxInfo, EnrichedProperty, PetrolCost, Property, SchoolInfo


def test_property_payload() -> None:
    payload = Property(
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
    payload = Property(url="https://www.rightmove.co.uk/properties/123")
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
    assert ep.town_description == ""
    assert ep.primary_ofsted == ""
    assert ep.secondary_ofsted == ""
    assert ep.epc_rating == ""
    assert ep.commute_breakdown is None


def test_transit_info() -> None:
    t = Commute(
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
    assert p.round_trip_minutes is None


def test_council_tax_info() -> None:
    c = CouncilTaxInfo(band="D", yearly_cost=2000.0, evidence_url="https://gov.uk/council-tax-bands")
    assert c.band == "D"
    assert c.yearly_cost == 2000.0
    assert c.evidence_url == "https://gov.uk/council-tax-bands"


def test_commute_breakdown() -> None:
    b = CommuteBreakdown(
        simon_daily_gbp=15.0,
        lorena_daily_gbp=24.0,
        bracknell_daily_gbp=10.0,
        yearly_total_gbp=3358.0,
        formula_explanation="46wk x (1x10.0 + 1x15.0 + 2x24.0)",
    )
    assert b.simon_daily_gbp == 15.0
    assert b.lorena_daily_gbp == 24.0
    assert b.bracknell_daily_gbp == 10.0
    assert b.yearly_total_gbp == 3358.0
    assert "46wk" in b.formula_explanation
