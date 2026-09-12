"""Failing first: the two live-proven stale provenances after a 0-day what-if.

Live box evidence (90970053, persons Pimlico trips=0): the persisted
commute_breakdown provenance carries
  (a) 25 frozen per-commute '... to Pimlico · 1x/wk ...' values (the nested
      journey chain re-priced with a live 0-trip destination but its POI
      stamp still says trips=1), and
  (b) the persons 'Household members' projection
      'Pimlico — <address>' — the address-only projection that drops the
      0-trip fact entirely, so a reader sees Pimlico listed with no hint
      the scenario zeroed it.

Approved semantics (user): a 0-days destination is NOT commuted — the
breakdown value already prices it at £0 by the multiplication, and its
provenance must show 0x/wk = £0, never 1 day a week.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from money import Money

from houses.geopoint import GeoPoint
from houses.model.domain import HomeCoOwner, Person, PlaceOfInterest
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property
from houses.server import app
from houses.web.auth import get_serializer
from tests.unit.conftest import flush_all

_LAT, _LON = 51.4934, -0.0098


def _poi(label: str, trips: int, address: str = "Pimlico Rd, London") -> PlaceOfInterest:
    return PlaceOfInterest(
        label=label,
        address=address,
        trips_per_week=trips,
        weeks_per_year=46,
        acceptable_modes=("car",),
    )


def _simon(pois: tuple) -> Person:
    return Person(
        name="Simon",
        has_car=True,
        email="simon@example.com",
        is_superuser=True,
        home_sale_price=Money(amount="550000", currency="GBP"),
        outstanding_mortgage=Money(amount="373000", currency="GBP"),
        home_co_owners=(HomeCoOwner(name="Lorena", share=50),),
        places_of_interest=pois,
    )


def _lorena() -> Person:
    return Person(
        name="Lorena",
        has_car=False,
        email="lorena@example.com",
        places_of_interest=(
            PlaceOfInterest(
                label="Aldgate",
                address="Aldgate station, London",
                trips_per_week=2,
                weeks_per_year=46,
                acceptable_modes=("walk",),
            ),
        ),
    )


def _world(rid: str) -> TestClient:
    from houses.services_provider import get_services as _gs

    _gs().persons_source.push(
        [
            _simon(
                (
                    _poi("Pimlico", 1),
                    _poi("Bracknell", 1, "Broad Lane, Bracknell"),
                    _poi("Dad", 1),
                )
            ),
            _lorena(),
        ],
        "user",
    )
    prop = PropertyNodes(rid)
    prop.rightmove_price.push(Money(amount="500000", currency="GBP"), "test")
    prop.rightmove_address.push("1 Test St", "test")
    prop.rightmove_bedrooms.push("3", "test")
    prop.rightmove_location.push(GeoPoint(_LAT, _LON), "test")
    prop.corrected_address.push("1 Test St, SW1V 2QQ", "test")
    prop.precise_location.push(GeoPoint(_LAT, _LON), "test")
    prop.user_entered_address.push("1 Test St, SW1V 2QQ", "test")
    prop.works_estimates.push({}, "test")
    prop.rental_income.push(Money(amount="0", currency="GBP"), "test")
    prop.comment_status.push("", "test")
    register_property(rid, prop)
    flush_all()
    client = TestClient(app)
    client.cookies.set(
        "session",
        get_serializer().dumps(
            {
                "email": "simon@example.com",
                "name": "Simon",
                "picture": "",
                "is_superuser": True,
                "impersonating": None,
            }
        ),
    )
    return client


def _claims(prov: dict) -> list[str]:
    parts = [str(prov.get("label") or ""), str(prov.get("value") or "")]
    formula = prov.get("formula") or {}
    parts.append(str(formula.get("result") or ""))
    parts.extend(f"{fl.get('label', '')} {fl.get('value', '')}" for fl in formula.get("lines") or [])
    for child in (prov.get("sources") or {}).values():
        parts.extend(_claims(child))
    return [p for p in parts if p]


def test_zero_day_provenance_shows_0x_and_never_1x():
    client = _world("42ZZ0001")
    assert (
        client.post(
            "/api/what-if/apply",
            json={
                "persons": [
                    {
                        "name": "Simon",
                        "places_of_interest": [
                            {
                                "label": "Pimlico",
                                "address": "Pimlico Rd, London",
                                "trips_per_week": 0,
                                "weeks_per_year": 46,
                                "acceptable_modes": ["car"],
                            },
                        ],
                    },
                ]
            },
        ).status_code
        == 200
    )
    flush_all()
    mcc = client.get("/api/properties/42ZZ0001/detail").json()["affordability"]["monthly_commute_cost"]
    assert mcc["succeeded"], mcc.get("error")
    claims = _claims(mcc["provenance"])
    stale = [s for s in claims if "Pimlico" in s and "1x/wk" in s]
    assert not stale, f"provenance claims Pimlico at 1x/wk after a 0-day what-if: {stale[:4]}"
    pimlico = [s for s in claims if "Pimlico" in s and "0x/wk" in s]
    assert pimlico, "the 0-day destination must appear at 0x/wk in its own provenance"


def test_zero_day_persons_projection_names_the_zero():
    client = _world("42ZZ0002")
    assert (
        client.post(
            "/api/what-if/apply",
            json={
                "persons": [
                    {
                        "name": "Simon",
                        "places_of_interest": [
                            {
                                "label": "Pimlico",
                                "address": "Pimlico Rd, London",
                                "trips_per_week": 0,
                                "weeks_per_year": 46,
                                "acceptable_modes": ["car"],
                            },
                        ],
                    },
                ]
            },
        ).status_code
        == 200
    )
    flush_all()
    mcc = client.get("/api/properties/42ZZ0002/detail").json()["affordability"]["monthly_commute_cost"]
    claims = _claims(mcc["provenance"])
    pimlico_places = [s for s in claims if "Pimlico —" in s]
    assert pimlico_places, "fixture must carry a Pimlico persons-projection for the check to mean anything"
    assert all("0x/wk" in s or "0 days" in s for s in pimlico_places), (
        f"the persons projection lists Pimlico with no hint it is zeroed: {pimlico_places[:3]}"
    )


def test_zero_day_delta_provenance_exists():
    """Failing first (defect 1): the monthly difference vs the current
    home has NO provenance widget — the vs-row shows the arithmetic in a
    title tooltip only. The delta derivation (candidate − baseline, and
    which baseline figures) must be explainable through the standard
    ProvenanceToggle like every other number."""
    client = _world("42ZZ0003")
    # A second, current-status property is the baseline the delta needs.
    home = PropertyNodes("42ZZ0004")
    home.rightmove_price.push(Money(amount="400000", currency="GBP"), "test")
    home.rightmove_address.push("2 Home St", "test")
    home.rightmove_bedrooms.push("3", "test")
    home.rightmove_location.push(GeoPoint(_LAT, _LON), "test")
    home.corrected_address.push("2 Home St, SW1P 1AA", "test")
    home.precise_location.push(GeoPoint(_LAT, _LON), "test")
    home.user_entered_address.push("2 Home St, SW1P 1AA", "test")
    home.works_estimates.push({}, "test")
    home.rental_income.push(Money(amount="0", currency="GBP"), "test")
    home.comment_status.push("current", "test")
    register_property("42ZZ0004", home)
    flush_all()
    detail = client.get("/api/properties/42ZZ0003/detail").json()
    delta = ((detail.get("affordability") or {}).get("group_monthly_cost") or {}).get("value", {}).get("delta_vs_home")
    assert delta is not None, "fixture must carry a delta for the provenance check to mean anything"
    prov = delta.get("couple", {}).get("provenance") if isinstance(delta.get("couple"), dict) else None
    assert prov is not None, "the monthly difference carries no provenance — add delta_vs_home provenance to the wire"


def test_zero_day_provenance_survives_recompute_without_replan():
    """The patched provenance must persist: a later recompute that does
    NOT re-plan (trips-only staleness leaves the chain parked) must
    still serve the patched 0x/wk strings, never the frozen 1x/wk."""
    from dag.scheduler import get_scheduler

    client = _world("42ZZ0010")
    client.post(
        "/api/what-if/apply",
        json={
            "persons": [
                {
                    "name": "Simon",
                    "places_of_interest": [
                        {
                            "label": "Pimlico",
                            "address": "Pimlico Rd, London",
                            "trips_per_week": 0,
                            "weeks_per_year": 46,
                            "acceptable_modes": ["car"],
                        },
                    ],
                },
            ]
        },
    )
    flush_all()
    first = client.get("/api/properties/42ZZ0010/detail").json()["affordability"]["monthly_commute_cost"]
    assert first["succeeded"]
    # Force the breakdown to rebuild its provenance from the parked chain
    # (no re-plan: the journey nodes stay untouched by construction).
    sched = get_scheduler()
    node = sched.registered_nodes().get("42ZZ0010/commute_breakdown")
    assert node is not None
    import asyncio

    asyncio.get_event_loop().run_until_complete(node.build_provenance())
    second = client.get("/api/properties/42ZZ0010/detail").json()["affordability"]["monthly_commute_cost"]
    stale = [s for s in _claims(second["provenance"]) if "Pimlico" in s and "1x/wk" in s]
    assert not stale, f"rebuilt provenance regressed to 1x/wk: {stale[:4]}"
