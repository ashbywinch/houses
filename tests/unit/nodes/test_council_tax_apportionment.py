"""Council tax apportionment: the bill must always be paid by someone.

User report (2026-09-09) on 2 Huntingdon Gardens (band F main + band A
annexe, all three adults paying both bills equally): after an address
update the annexe bill (£1,670/yr) silently vanished from the figures —
the group monthly carried only the main bill split three ways — and the
provenance said nothing about who pays what, so the numbers could not
be checked. The payer settings on that property were empty.

Rules under test:
- Empty or stale payer settings mean the bill splits across ALL adults
  (the main bill already behaved this way; the annexe must too). A
  council tax bill is always paid by someone.
- Named payers split their bill by count; the rest goes to the other
  adults.
- The annexe can be explicitly ignored.
- The provenance names the annexe payers so the split is checkable.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from fastapi.testclient import TestClient
from money import Money

from dag.attempt import Attempt
from dag.measurement import Measurement
from houses.council_tax_info import AnnexeDwelling, CouncilTaxInfo
from houses.geopoint import GeoPoint
from houses.model.domain import HomeCoOwner, Person, PlaceOfInterest
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property
from houses.server import app
from houses.services_provider import get_services
from houses.web.auth import get_serializer
from tests.unit.conftest import flush_all

_TEST_LAT = 51.4000
_TEST_LON = -1.3230
_MAIN_YEARLY = Decimal("3618.33")
_ANNEXE_YEARLY = Decimal("1670.00")
_BOTH_MONTHLY = (_MAIN_YEARLY + _ANNEXE_YEARLY) / 12
_ADULTS = ["Simon", "Lorena", "Ashby"]


class _FakeCouncilTax:
    def __init__(self) -> None:
        self.result: CouncilTaxInfo = _council_tax_result()

    async def lookup(self, postcode: str, address: str = "") -> Attempt[CouncilTaxInfo]:
        return Attempt.succeeded(self.result)


def _council_tax_result() -> CouncilTaxInfo:
    return CouncilTaxInfo(
        band="F",
        yearly_cost=Measurement(Money(_MAIN_YEARLY, "GBP"), 0.0),
        evidence_url="https://council-tax.test/west-berkshire",
        annexe=AnnexeDwelling(
            address="ANNEXE, 2 HUNTINGDON GARDENS",
            band="A",
            yearly_cost=Measurement(Money(_ANNEXE_YEARLY, "GBP"), 0.0),
        ),
    )


def _prime(
    rid: str,
    *,
    main_payers: list[str],
    annexe_payers: list[str],
) -> PropertyNodes:
    svc = get_services()
    svc.council_tax_service = cast(Any, _FakeCouncilTax())
    svc.persons_source.push(
        [
            Person(
                name="Simon",
                has_car=True,
                email="simon@example.com",
                is_superuser=True,
                home_sale_price=Money(amount="550000", currency="GBP"),
                outstanding_mortgage=Money(amount="373000", currency="GBP"),
                home_co_owners=(HomeCoOwner(name="Lorena", share=50),),
                places_of_interest=(
                    PlaceOfInterest(
                        label="Pimlico",
                        address="Pimlico Rd, London",
                        trips_per_week=1,
                        weeks_per_year=46,
                        acceptable_modes=("car",),
                    ),
                ),
            ),
            Person(name="Lorena", has_car=False, email="lorena@example.com"),
            Person(
                name="Ashby",
                has_car=False,
                email="ashby@example.com",
                cash_contribution=Money(amount="300000", currency="GBP"),
            ),
        ],
        "user",
    )
    prop = PropertyNodes(rid)
    prop.rightmove_price.push(Money(amount="500000", currency="GBP"), "test")
    prop.rightmove_address.push("2 Huntingdon Gardens", "test")
    prop.rightmove_bedrooms.push("4", "test")
    prop.rightmove_location.push(GeoPoint(_TEST_LAT, _TEST_LON), "test")
    prop.corrected_address.push("2 Huntingdon Gardens, Newbury RG14 2RG", "test")
    prop.precise_location.push(GeoPoint(_TEST_LAT, _TEST_LON), "test")
    prop.user_entered_address.push("2 Huntingdon Gardens, Newbury RG14 2RG", "test")
    prop.works_estimates.push({}, "test")
    prop.rental_income.push(Money(amount="0", currency="GBP"), "test")
    prop.comment_status.push("", "test")
    prop.council_tax_payers.push(main_payers, "user")
    prop.annexe_payers.push(annexe_payers, "user")
    prop.annexe_ignored.push(False, "user")
    register_property(rid, prop)
    return prop


def _client() -> TestClient:
    client = TestClient(app)
    client.cookies.set("session", get_serializer().dumps({
        "email": "simon@example.com",
        "name": "Simon",
        "picture": "",
        "is_superuser": True,
        "impersonating": None,
    }))
    return client


def _group_council(detail: dict, group: str) -> Decimal:
    """The group's total council tax: main share + annexe share."""
    breakdown = detail["affordability"]["group_monthly_cost"]["value"][f"{group}_breakdown"]
    return Decimal(breakdown.get("council_tax") or 0) + Decimal(breakdown.get("annexe_council_tax") or 0)


def test_empty_payer_settings_split_both_bills_across_all_adults():
    """The seeded payer settings are empty: the annexe bill must still
    be paid — split across all adults like the main bill already is.
    (The live bug: the annexe silently vanished from the figures.)"""
    client = _client()
    _prime("42555556", main_payers=[], annexe_payers=[])
    flush_all()

    detail = client.get("/api/properties/42555556/detail").json()
    couple = _group_council(detail, "couple")
    others = _group_council(detail, "others")
    expected_couple = (_BOTH_MONTHLY * 2 / 3).quantize(Decimal("0.01"))
    expected_others = (_BOTH_MONTHLY / 3).quantize(Decimal("0.01"))
    assert abs(couple - expected_couple) < Decimal("0.05"), (
        f"the couple must carry 2/3 of both bills: got {couple}, "
        f"expected about {expected_couple}"
    )
    assert abs(others - expected_others) < Decimal("0.05"), (
        f"Ashby must carry 1/3 of both bills: got {others}, "
        f"expected about {expected_others}"
    )

    # THE PROVENANCE CONTRACT: the derivation must state the bills and
    # who pays, so the figures are checkable (P2, one step away).
    prov = detail["affordability"]["group_monthly_cost"]["provenance"]
    text = (prov.get("value") or "") + " " + (prov.get("description") or "")
    assert "council tax" in text.lower()
    for name in ("Simon", "Lorena", "Ashby"):
        assert name in text, f"the provenance must name the payer {name}"
    assert "3618.33" in text and "1670.00" in text, (
        "the provenance must state both bills"
    )


def test_all_three_paying_both_bills_split_equally():
    """With every adult named as a payer for both bills, each bill splits
    in thirds and the shares reach the right groups."""
    client = _client()
    _prime("42555556", main_payers=_ADULTS, annexe_payers=_ADULTS)
    flush_all()

    detail = client.get("/api/properties/42555556/detail").json()
    couple = _group_council(detail, "couple")
    others = _group_council(detail, "others")
    assert abs(couple - (_BOTH_MONTHLY * 2 / 3)) < Decimal("0.05")
    assert abs(others - (_BOTH_MONTHLY / 3)) < Decimal("0.05")


def test_named_payers_split_their_bills():
    """Main bill: Simon + Lorena. Annexe bill: Ashby alone. Each group's
    council share follows its own bill split."""
    client = _client()
    _prime("42555556", main_payers=["Simon", "Lorena"], annexe_payers=["Ashby"])
    flush_all()

    detail = client.get("/api/properties/42555556/detail").json()
    couple = _group_council(detail, "couple")
    others = _group_council(detail, "others")
    main_monthly = _MAIN_YEARLY / 12
    annexe_monthly = _ANNEXE_YEARLY / 12
    assert abs(couple - main_monthly) < Decimal("0.05"), (
        f"the couple carries the whole main bill split in half: {couple}"
    )
    assert abs(others - annexe_monthly) < Decimal("0.05"), (
        f"Ashby carries the whole annexe bill: {others}"
    )


def test_ignored_annexe_is_excluded():
    client = _client()
    prop = _prime("42555556", main_payers=_ADULTS, annexe_payers=_ADULTS)
    prop.annexe_ignored.push(True, "user")
    flush_all()

    detail = client.get("/api/properties/42555556/detail").json()
    couple = _group_council(detail, "couple")
    others = _group_council(detail, "others")
    main_monthly = _MAIN_YEARLY / 12
    assert abs(couple - (main_monthly * 2 / 3)) < Decimal("0.05"), (
        f"only the main bill is split when the annexe is ignored: {couple}"
    )
    assert abs(others - (main_monthly / 3)) < Decimal("0.05")


def test_stale_payer_names_do_not_vanish_the_bill():
    """Payer names that no longer match the household must not drop the
    bill: it falls back to the all-adults split."""
    client = _client()
    prop = _prime("42555556", main_payers=[], annexe_payers=[])
    prop.council_tax_payers.push(["Ghost"], "user")
    prop.annexe_payers.push(["Ghost"], "user")
    flush_all()

    detail = client.get("/api/properties/42555556/detail").json()
    couple = _group_council(detail, "couple")
    others = _group_council(detail, "others")
    assert abs(couple - (_BOTH_MONTHLY * 2 / 3)) < Decimal("0.05")
    assert abs(others - (_BOTH_MONTHLY / 3)) < Decimal("0.05")


def test_address_change_keeps_payer_settings_and_recomputes():
    """The address-change scenario from the report: a new address means
    a new bill — the payer settings survive and the shares follow the
    new bill."""
    fake = _FakeCouncilTax()
    client = _client()
    _prime("42555556", main_payers=_ADULTS, annexe_payers=_ADULTS)
    get_services().council_tax_service = cast(Any, fake)
    flush_all()

    # The address change lands: the lookup now returns a dearer bill.
    fake.result = CouncilTaxInfo(
        band="G",
        yearly_cost=Measurement(Money(_MAIN_YEARLY * 2, "GBP"), 0.0),
        annexe=AnnexeDwelling(
            address="ANNEXE, 2 HUNTINGDON GARDENS",
            band="A",
            yearly_cost=Measurement(Money(_ANNEXE_YEARLY, "GBP"), 0.0),
        ),
    )
    resp = client.patch(
        "/api/properties/42555556/address",
        json={"address": "24 Huntingdon Gardens, Newbury RG14 5TT"},
    )
    assert resp.status_code == 200
    flush_all()

    detail = client.get("/api/properties/42555556/detail").json()
    couple = _group_council(detail, "couple")
    others = _group_council(detail, "others")
    new_both_monthly = (_MAIN_YEARLY * 2 + _ANNEXE_YEARLY) / 12
    assert abs(couple - (new_both_monthly * 2 / 3)) < Decimal("0.05"), (
        f"the couple share must follow the new bill: {couple}"
    )
    assert abs(others - (new_both_monthly / 3)) < Decimal("0.05")

    # The payer settings survived the address change.
    payers = detail["council_tax_apportionment"]
    assert list(payers["main_payers"]["value"]) == _ADULTS
    assert list(payers["annexe_payers"]["value"]) == _ADULTS
