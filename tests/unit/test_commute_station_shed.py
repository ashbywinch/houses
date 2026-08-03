"""Station shed — keep-rule, bounding box, inner zone, and batch orchestration."""

from __future__ import annotations

import pytest

from houses.geo import GeoPoint
from tools.commute.station_shed import (
    BBox,
    Office,
    Station,
    build_shed,
    keep_station,
    load_stations,
)

PIMLICO = GeoPoint(51.4904, -0.1378)  # SW1V 2QQ approx
ALDGATE = GeoPoint(51.5145, -0.0762)  # EC3A 7LP approx
OFFICES = [Office("SW1V 2QQ", PIMLICO), Office("EC3A 7LP", ALDGATE)]
THRESHOLD = 132

BBOX = BBox(lat_min=50.0, lat_max=54.0, lon_min=-4.0, lon_max=2.0)

STATIONS_CSV = """stationName,lat,long,crsCode,iataAirportCode,constituentCountry
Reading,51.4599,-0.9705,RDG,,england
Guildford,51.2367,-0.5808,GLD,,england
Woking,51.3173,-0.5571,WOK,,england
Ealing Broadway,51.5149,-0.3016,EAL,,england
Exeter St Davids,50.7292,-3.5435,EXD,,england
"""


# ── Bounding box ─────────────────────────────────────────────────────


def test_bbox_contains_inside():
    assert BBOX.contains(51.4, -0.9)
    assert BBOX.contains(52.9, -0.6)  # Grantham — must be inside


def test_bbox_excludes_outside():
    assert not BBOX.contains(54.5, -1.5)  # north of York/Leeds — out
    assert not BBOX.contains(50.0, -5.0)  # west of Land's End — out


# ── Inner zone ───────────────────────────────────────────────────────


def test_in_inner_zone_within_radius():
    # Ealing Broadway is ~10 km from Pimlico — inside a 20 km inner zone.
    st = Station("Ealing Broadway", "EAL", 51.5149, -0.3016)
    assert st.distance_km_to(PIMLICO) <= 20.0
    assert st.distance_km_to(ALDGATE) <= 20.0


def test_in_inner_zone_outside_radius():
    # Woking is ~30+ km from both offices.
    st = Station("Woking", "WOK", 51.3173, -0.5571)
    assert st.distance_km_to(PIMLICO) > 20.0


# ── Keep rule ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("inner", "dur_p", "dur_a", "expected"),
    [
        (True, None, None, True),  # inner zone: kept without routing
        (False, 132, 132, True),  # boundary: at threshold — kept
        (False, 133, 132, True),  # min-of-destinations: Aldgate in
        (False, 132, 133, True),  # min-of-destinations: Pimlico in
        (False, 133, 133, False),  # both over — dropped
        (False, None, 100, True),  # only Aldgate routed — kept
        (False, 100, None, True),  # only Pimlico routed — kept
        (False, None, None, False),  # both failed — dropped
        (False, 133, None, False),  # one failed, one over — dropped
    ],
)
def test_keep_station(inner, dur_p, dur_a, expected):
    assert keep_station(inner, dur_p, dur_a, THRESHOLD) is expected


# ── Orchestration ────────────────────────────────────────────────────


class _CountingRouter:
    """Fake router recording calls; durations keyed by (crs, dest_postcode)."""

    def __init__(self, durations: dict[tuple[str, str], int | None]):
        self.durations = durations
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, station: Station, dest: str) -> int | None:
        self.calls.append((station.crs, dest))
        return self.durations.get((station.crs, dest))


DEST_P = "SW1V 2QQ"
DEST_A = "EC3A 7LP"


def _make_stations() -> list[Station]:
    return [
        Station("Reading", "RDG", 51.4599, -0.9705),
        Station("Guildford", "GLD", 51.2367, -0.5808),
        Station("Woking", "WOK", 51.3173, -0.5571),
        Station("Ealing Broadway", "EAL", 51.5149, -0.3016),
        Station("Exeter St Davids", "EXD", 50.7292, -3.5435),
    ]


@pytest.mark.asyncio
async def test_build_shed_routes_only_non_inner_in_bbox():
    router = _CountingRouter(
        {
            ("RDG", DEST_P): 55, ("RDG", DEST_A): 60,
            ("GLD", DEST_P): 70, ("GLD", DEST_A): 75,
            ("WOK", DEST_P): 60, ("WOK", DEST_A): 65,
            ("EXD", DEST_P): 150, ("EXD", DEST_A): 155,
        }
    )
    shed = await build_shed(_make_stations(), OFFICES, BBOX, 20.0, THRESHOLD, router)

    by_crs = {r["crs"]: r for r in shed}

    # Ealing Broadway is inner-zone: kept with no routing calls at all.
    assert by_crs["EAL"]["kept"] is True
    assert by_crs["EAL"]["duration_pimlico"] is None

    # Reading (55/60) kept; Guildford (70/75) kept; Woking (60/65) kept.
    assert by_crs["RDG"]["kept"] is True
    assert by_crs["GLD"]["kept"] is True
    assert by_crs["WOK"]["kept"] is True

    # Exeter (150/155) dropped — the negative control.
    assert by_crs["EXD"]["kept"] is False

    # Routed calls: 4 stations × 2 destinations; EAL never routed.
    routed_crs = {c[0] for c in router.calls}
    assert routed_crs == {"RDG", "GLD", "WOK", "EXD"}
    assert "EAL" not in routed_crs
    assert len(router.calls) == 8


@pytest.mark.asyncio
async def test_build_shed_failed_route_dropped():
    router = _CountingRouter({("RDG", DEST_P): None, ("RDG", DEST_A): None})
    shed = await build_shed(_make_stations(), OFFICES, BBOX, 20.0, THRESHOLD, router)
    by_crs = {r["crs"]: r for r in shed}
    assert by_crs["RDG"]["kept"] is False


@pytest.mark.asyncio
async def test_build_shed_deterministic():
    async def router(station, dest):
        return 60

    a = await build_shed(_make_stations(), OFFICES, BBOX, 20.0, THRESHOLD, router)
    b = await build_shed(_make_stations(), OFFICES, BBOX, 20.0, THRESHOLD, router)
    assert a == b


# ── CSV loading ──────────────────────────────────────────────────────


def test_load_stations_parses_csv(tmp_path):
    csv_path = tmp_path / "stations.csv"
    csv_path.write_text(STATIONS_CSV)
    stations = load_stations(csv_path)
    assert len(stations) == 5
    reading = next(s for s in stations if s.crs == "RDG")
    assert reading.name == "Reading"
    assert reading.lat == 51.4599
    assert reading.lon == -0.9705
