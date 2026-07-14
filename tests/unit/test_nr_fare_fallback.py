"""TransitNode must apply NR fare enrichment when TfL returns £0 cost."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.derived_node import flush_processor
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.nodes.property import PropertyNodes
from houses.property_registry import _registry, register_property


@pytest.fixture(autouse=True)
def _fresh_db():
    import sqlite3

    import dag.persistence as per
    saved = per._get_db
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    per._get_db = lambda: conn
    per.init_db()
    yield
    per._get_db = saved


@pytest.fixture(autouse=True)
def _clear():
    _registry.clear()
    yield
    _registry.clear()


@pytest.fixture(autouse=True)
def _mock():
    from houses.services_provider import _request_services as _sp
    from tests.helpers import make_services

    class _ZeroCostRouter:
        async def route(self, origin, destination, *, has_car, max_walk_minutes):
            return Attempt.succeeded(Commute(
                person=Person(name="Simon", has_car=has_car),
                label="Pimlico",
                destination=PlaceOfInterest(label="Pimlico", postcode=str(destination)),
                duration=Quantity(71, "minute"),
                daily_cost=Money("0", "GBP"),
                mode="transit",
            ))

    svc = make_services(commute_router=_ZeroCostRouter())
    token = _sp.set(svc)
    yield
    _sp.reset(token)


@pytest.mark.asyncio
async def test_transit_node_applies_nr_fare_fallback():
    from houses.rail_fares import RailFareRegistry
    from houses.stations import StationRegistry

    rid = "nr_fare_test"
    prop = PropertyNodes(rid)
    prop.rightmove_address.push("St James Close, Woking, GU21 7QF", "Rightmove")
    prop.rightmove_url.push("https://rightmove.co.uk/nr_fare_test", "Browser")
    prop.rightmove_bedrooms.push("3", "Rightmove")
    prop.rightmove_price.push("500000", "Rightmove")
    prop.rightmove_location.push(GeoPoint(51.317, -0.556), "Rightmove map")
    register_property(rid, prop)

    tmp = Path(tempfile.mkdtemp())
    (tmp / "stations.csv").write_text(
        "stationName,crsCode,lat,long\nWoking,WOK,51.317,-0.556\nVictoria Station,VIC,51.495,-0.144\n"
    )
    (tmp / "fares.csv").write_text("origin_crs,dest_crs,single_fare_gbp\nWOK,VIC,17.00\n")

    reg = RailFareRegistry(
        station_registry=StationRegistry(_stations_csv=tmp / "stations.csv"),
        _fares_csv=tmp / "fares.csv",
    )

    async def mock_geocode(_):
        return Attempt.succeeded(GeoPoint(51.317, -0.556))

    async def mock_tube_fare(station, postcode, _data=None):
        return None

    for tn in prop._transit_nodes:
        tn._nr_registry = reg
        tn._nr_geocode = mock_geocode
        tn._nr_tube_fare = mock_tube_fare

    await flush_processor()
    await flush_processor()

    transit_node = None
    for tn in prop._transit_nodes:
        if "Simon/Pimlico" in tn._id:
            transit_node = tn
            break
    assert transit_node is not None

    a = await transit_node.attempt()
    assert a.succeeded
    val = a.value_or_none()
    assert val is not None

    dc = val.get("daily_cost", {})
    amount = dc.get("amount", 0)
    assert amount == 39.60, f"Expected 39.60, got {amount}. daily_cost={dc}"
