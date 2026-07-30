"""Tests for domain model classes in houses/model/domain.py.

Every new domain class must have a failing test before implementation.
These tests verify construction and field access only — no DAG logic.
"""

from money import Money
from pint import Quantity

from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.model.domain import (
    Commute,
    EpcRating,
    Person,
    PlaceOfInterest,
    Property,
    RightmoveProperty,
    Schools,
    Walkability,
)
from houses.school import School
from houses.school_gender import SchoolGender


class TestPerson:
    def test_construction_with_all_fields(self):
        poi = PlaceOfInterest(label="Office", address="EC3A 7LP")
        person = Person(
            name="Simon",
            has_car=True,
            home_sale_price=Money("500000", "GBP"),
            outstanding_mortgage=Money("200000", "GBP"),
            cash_contribution=Money("50000", "GBP"),
            works_estimate_required=True,
            places_of_interest=(poi,),
        )
        assert person.name == "Simon"
        assert person.has_car is True
        assert person.home_sale_price == Money("500000", "GBP")
        assert person.outstanding_mortgage == Money("200000", "GBP")
        assert person.cash_contribution == Money("50000", "GBP")
        assert person.works_estimate_required is True
        assert person.places_of_interest == (poi,)

    def test_minimal_construction(self):
        person = Person(name="Simon", has_car=False)
        assert person.name == "Simon"
        assert person.has_car is False
        assert person.home_sale_price == Money("0", "GBP")
        assert person.places_of_interest == ()


class TestPlaceOfInterest:
    def test_construction(self):
        poi = PlaceOfInterest(label="Office", address="EC3A 7LP")
        assert poi.label == "Office"
        assert poi.address == "EC3A 7LP"


class TestCommute:
    def test_construction(self):
        person = Person(name="Simon", has_car=True)
        destination = PlaceOfInterest(label="Office", address="EC3A 7LP")
        leg = JourneyLeg(
            mode=LegMode.TUBE,
            duration=Quantity(15, "minute"),
            line_name="Central",
            end_station="Bank",
        )
        cost_group = CostGroup(legs=(leg,), operator="TfL", cost=3.50)
        commute = Commute(
            person=person,
            label="Office",
            destination=destination,
            duration=Quantity(32, "minute"),
            daily_cost=Money("4.50", "GBP"),
            _details=(cost_group,),
        )
        assert commute.person == person
        assert commute.label == "Office"
        assert commute.destination == destination
        assert commute.duration == Quantity(32, "minute")
        assert commute.daily_cost == Money("4.50", "GBP")
        assert commute.details == (cost_group,)

    def test_empty_details(self):
        person = Person(name="Simon", has_car=True)
        dest = PlaceOfInterest(label="Office", address="EC3A 7LP")
        commute = Commute(
            person=person,
            label="Office",
            destination=dest,
            duration=Quantity(0, "minute"),
            daily_cost=Money(0, "GBP"),
        )
        assert commute.details == ()


class TestRightmoveProperty:
    def test_construction_with_all_fields(self):
        rp = RightmoveProperty(
            url="https://www.rightmove.co.uk/properties/123",
            rid="123",
            address="High Street, Some Town",
            postcode="RG14 1AA",
            bedrooms=3,
            price=650_000.0,
            latitude=51.5,
            longitude=-1.0,
        )
        assert rp.url == "https://www.rightmove.co.uk/properties/123"
        assert rp.rid == "123"
        assert rp.address == "High Street, Some Town"
        assert rp.postcode == "RG14 1AA"
        assert rp.bedrooms == 3
        assert rp.price == 650_000.0
        assert rp.latitude == 51.5
        assert rp.longitude == -1.0

    def test_minimal_construction(self):
        rp = RightmoveProperty(url="https://www.rightmove.co.uk/properties/456")
        assert rp.url == "https://www.rightmove.co.uk/properties/456"
        assert rp.rid == ""
        assert rp.address == ""
        assert rp.bedrooms is None
        assert rp.latitude is None


class TestProperty:
    def test_construction(self):
        rp = RightmoveProperty(
            url="https://www.rightmove.co.uk/properties/123",
            price=650_000.0,
        )
        prop = Property(
            rid="123",
            rightmove_property=rp,
            address="High Street",
            postcode="RG14 1AA",
            bedrooms=3,
            price=650_000.0,
            latitude=51.5,
            longitude=-1.0,
        )
        assert prop.rid == "123"
        assert prop.rightmove_property is rp
        assert prop.address == "High Street"
        assert prop.postcode == "RG14 1AA"
        assert prop.bedrooms == 3
        assert prop.price == 650_000.0
        assert prop.latitude == 51.5
        assert prop.longitude == -1.0

    def test_minimal_construction(self):
        prop = Property(rid="123")
        assert prop.rid == "123"
        assert prop.address == ""
        assert prop.bedrooms is None
        assert prop.rightmove_property is None


class TestSchools:
    def test_construction_with_both(self):
        primary = School(
            urn="123456",
            name="Test Primary",
            phase="Primary",
            gender=SchoolGender.MIXED,
            type_of_establishment="Community School",
            postcode="RG14 1AA",
            website="",
            ofsted_rating="Outstanding",
            inspection_year="2023",
            coords=None,
            statutory_low_age=None,
            statutory_high_age=None,
        )
        secondary = School(
            urn="789012",
            name="Test Secondary",
            phase="Secondary",
            gender=SchoolGender.MIXED,
            type_of_establishment="Academy Converter",
            postcode="RG14 2BB",
            website="",
            ofsted_rating="Good",
            inspection_year="2022",
            coords=None,
            statutory_low_age=None,
            statutory_high_age=None,
        )
        schools = Schools(primary=primary, secondary=secondary)
        assert schools.primary.name == "Test Primary"
        assert schools.secondary.name == "Test Secondary"

    def test_defaults(self):
        schools = Schools()
        assert schools.primary is None
        assert schools.secondary is None


class TestEpcRating:
    def test_construction(self):
        epc = EpcRating(
            rating="C",
            potential_rating="B",
            evidence_url="https://gov.uk/epc/123",
        )
        assert epc.rating == "C"
        assert epc.potential_rating == "B"
        assert epc.evidence_url == "https://gov.uk/epc/123"

    def test_defaults(self):
        epc = EpcRating()
        assert epc.rating == ""
        assert epc.potential_rating == ""
        assert epc.evidence_url == ""


class TestWalkability:
    def test_construction(self):
        w = Walkability(
            walk_to_town=Quantity(15, "minute"),
            amenities="Supermarket, Park",
            town_description="A lovely town",
        )
        assert w.walk_to_town == Quantity(15, "minute")
        assert w.amenities == "Supermarket, Park"
        assert w.town_description == "A lovely town"

    def test_defaults(self):
        w = Walkability()
        assert w.walk_to_town is None
        assert w.amenities == ""
        assert w.town_description == ""
