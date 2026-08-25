"""Tests for data models."""

from money import Money

from houses.commute import CommuteBreakdown
from houses.council_tax_info import CouncilTaxInfo
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.property import EnrichedProperty, Property
from houses.school import School
from houses.school_gender import SchoolGender


def test_property_payload() -> None:
    payload = Property(
        url="https://www.rightmove.co.uk/properties/123",
        address="High Street, Some Town, RG14 1AA",
        bedrooms=3,
        price=Money("650000", "GBP"),
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
    assert ep.price == Money("0", "GBP")
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
    from pint import Quantity

    t = Commute(
        person=Person(name="Test", has_car=False),
        label="Test",
        destination=PlaceOfInterest(label="Test", address="SW1V 2QQ"),
        duration=Quantity(10, "minute"),
        daily_cost=Money("0", "GBP"),
        mode="transit",
    )
    assert t.mode == "transit"


def test_school_defaults() -> None:
    """School.from_GIAS_row should handle missing fields gracefully."""
    school = School.from_GIAS_row({})
    assert school.name == ""
    assert school.urn == ""
    assert school.gender == SchoolGender.UNKNOWN
    assert not school.fee_paying
    assert school.coords is None
    assert school.full_address == ""


def test_school_from_gias_row_captures_full_address() -> None:
    """The school's full address must be captured from the GIAS columns
    when the school is first loaded — the walk/drive leg destination
    comes from here, never from a lat/lon."""
    school = School.from_GIAS_row(
        {
            "EstablishmentName": "Larchfield Primary School",
            "Street": "Bargeman Road",
            "Locality": "Maidenhead",
            "Town": "Maidenhead",
            "County (name)": "Windsor and Maidenhead",
            "Postcode": "SL6 4ET",
            "Latitude": "51.52",
            "Longitude": "-0.72",
        }
    )
    assert school.name == "Larchfield Primary School"
    assert school.postcode == "SL6 4ET"
    assert school.full_address == (
        "Bargeman Road, Maidenhead, Maidenhead, Windsor and Maidenhead, SL6 4ET"
    )


def test_school_from_gias_row_captures_website_as_url() -> None:
    """The GIAS SchoolWebsite column must populate the school's url so
    the detail page links to the school's own site — not the current
    page. Regression: url stayed '' while website held the value."""
    school = School.from_GIAS_row(
        {
            "EstablishmentName": "Chiltern Wood School",
            "SchoolWebsite": "www.chilternwood.bucks.sch.uk",
            "Latitude": "51.62",
            "Longitude": "-0.77",
        }
    )
    assert school.website == "www.chilternwood.bucks.sch.uk"
    assert school.url == "https://www.chilternwood.bucks.sch.uk"


def test_school_default_url_empty_without_website() -> None:
    """No SchoolWebsite column → url stays empty (the frontend renders
    the name as plain text, never a dead link)."""
    school = School.from_GIAS_row({})
    assert school.url == ""


def test_bracknell_commute_defaults() -> None:
    from money import Money
    from pint import Quantity

    p = Commute(
        person=Person(name="", has_car=True),
        label="Bracknell",
        destination=PlaceOfInterest(label="Bracknell", address="RG12 8YA"),
        duration=Quantity(10, "minute"),
        daily_cost=Money("0", "GBP"),
        mode="drive",
    )
    assert p.label == "Bracknell"
    assert p.daily_cost == Money("0", "GBP")
    assert p.duration.magnitude == 10


def test_council_tax_info() -> None:
    from money import Money

    from dag.measurement import Measurement

    c = CouncilTaxInfo(
        band="D",
        yearly_cost=Measurement(Money("2000", "GBP"), 0.0),
        evidence_url="https://gov.uk/council-tax-bands",
    )
    assert c.band == "D"
    assert c.yearly_cost == Measurement(Money("2000", "GBP"), 0.0)
    assert c.evidence_url == "https://gov.uk/council-tax-bands"


def test_commute_breakdown() -> None:
    from money import Money

    b = CommuteBreakdown(
        simon_daily_gbp=Money("15", "GBP"),
        lorena_daily_gbp=Money("24", "GBP"),
        bracknell_daily_gbp=Money("10", "GBP"),
        yearly_total_gbp=Money("3358", "GBP"),
        formula_explanation="46wk x (1x10.0 + 1x15.0 + 2x24.0)",
    )
    assert b.simon_daily_gbp == Money("15", "GBP")
    assert b.lorena_daily_gbp == Money("24", "GBP")
    assert b.bracknell_daily_gbp == Money("10", "GBP")
    assert b.yearly_total_gbp == Money("3358", "GBP")
    assert "46wk" in b.formula_explanation
