"""Resume/checkpoint behaviour — a killed batch must not redo completed work."""

from __future__ import annotations

import pytest

from houses.geopoint import GeoPoint
from tools.commute.station_shed import BBox, Office, RoutingContext, Station, build_shed

PIMLICO = GeoPoint(51.4904, -0.1378)
ALDGATE = GeoPoint(51.5145, -0.0762)
OFFICES = [Office("SW1V 2QQ", PIMLICO), Office("EC3A 7LP", ALDGATE)]
BBOX = BBox(lat_min=50.0, lat_max=54.0, lon_min=-4.0, lon_max=2.0)
THRESHOLD = 132


def _ctx(router) -> RoutingContext:
    return RoutingContext(
        offices=OFFICES,
        bbox=BBOX,
        inner_radius_km=20.0,
        threshold=THRESHOLD,
        router=router,
        delay_s=0,
    )

STATIONS = [
    Station("Reading", "RDG", 51.4599, -0.9705),
    Station("Guildford", "GLD", 51.2367, -0.5808),
    Station("Woking", "WOK", 51.3173, -0.5571),
    Station("Ealing Broadway", "EAL", 51.5149, -0.3016),
    Station("Exeter St Davids", "EXD", 50.7292, -3.5435),
]

DURATIONS = {
    ("RDG", "SW1V 2QQ"): 55, ("RDG", "EC3A 7LP"): 60,
    ("GLD", "SW1V 2QQ"): 70, ("GLD", "EC3A 7LP"): 75,
    ("WOK", "SW1V 2QQ"): 60, ("WOK", "EC3A 7LP"): 65,
    ("EXD", "SW1V 2QQ"): 150, ("EXD", "EC3A 7LP"): 155,
}


class _CountingRouter:
    def __init__(self, durations: dict[tuple[str, str], int | None] | None = None):
        self.durations = durations if durations is not None else DURATIONS
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, station: Station, dest: str) -> int | None:
        self.calls.append((station.crs, dest))
        return self.durations.get((station.crs, dest))


@pytest.mark.asyncio
async def test_resume_skips_stations_already_in_checkpoint():
    router = _CountingRouter()
    # First run processes everything.
    full = await build_shed(STATIONS, _ctx(router))
    assert len(router.calls) == 8  # 4 routed stations × 2 destinations (EAL inner)

    # Second run with the first two records as the checkpoint: the checkpointed
    # stations are never re-routed; only the remaining ones are.
    router.calls.clear()
    resumed = await build_shed(STATIONS, _ctx(router), existing_records=full[:2])
    assert all(c[0] in {"WOK", "EXD"} for c in router.calls)  # RDG/GLD/EAL not re-routed
    assert len(router.calls) == 4  # Woking + Exeter × 2 destinations
    assert resumed == full  # and the outcome is identical to a from-scratch run


@pytest.mark.asyncio
async def test_resume_routes_only_remaining_stations():
    router = _CountingRouter()
    full = await build_shed(STATIONS, _ctx(router))
    checkpoint = full[:3]  # Reading, Guildford, Woking done

    router2 = _CountingRouter()
    resumed = await build_shed(STATIONS, _ctx(router2), existing_records=checkpoint)
    # Only Exeter (EXD × 2 destinations) is routed; EAL is inner-zone (kept, no calls).
    assert sorted(router2.calls) == [("EXD", "EC3A 7LP"), ("EXD", "SW1V 2QQ")]
    assert resumed == full


@pytest.mark.asyncio
async def test_resume_when_checkpoint_complete_makes_no_calls():
    router = _CountingRouter()
    full = await build_shed(STATIONS, _ctx(router))
    router.calls.clear()
    resumed = await build_shed(STATIONS, _ctx(router), existing_records=full)
    assert router.calls == []
    assert resumed == full


@pytest.mark.asyncio
async def test_implausible_durations_rejected():
    """A route faster than ~150 km/h door-to-door is not physically possible.

    TfL's name-origin fallback can resolve to the wrong place (observed:
    Worcestershire Parkway, ~160 km out, reported 35 min). Such durations must
    be treated as failed, not kept, or coverage extends to the wrong area.
    """
    # 160 km straight-line from Pimlico; 35 min = ~274 km/h — impossible.
    far = [Station("Worcestershire Parkway", "WOP", 52.14, -2.18), *STATIONS[1:]]
    router = _CountingRouter(
        {
            ("WOP", "SW1V 2QQ"): 35,
            ("WOP", "EC3A 7LP"): 60,
            **{k: v for k, v in DURATIONS.items() if k[0] != "WOP"},
        }
    )
    shed = await build_shed(far, _ctx(router))
    wop = next(r for r in shed if r["crs"] == "WOP")
    assert wop["kept"] is False
    assert wop["routing_error"] == "failed"
    assert wop["duration_pimlico"] is None


@pytest.mark.asyncio
async def test_plausible_durations_accepted():
    """Legit fast rail (Reading, ~60 km at 55 min) must survive the floor."""
    shed = await build_shed(STATIONS, _ctx(_CountingRouter()))
    rdg = next(r for r in shed if r["crs"] == "RDG")
    assert rdg["kept"] is True
    assert rdg["duration_pimlico"] == 55


@pytest.mark.asyncio
async def test_resume_reroutes_failed_stations():
    """A record with routing_error is NOT done: a resume re-routes the station."""
    # First run: EXD fails both destinations (not in the router's durations).
    failed_router = _CountingRouter({k: v for k, v in DURATIONS.items() if k[0] != "EXD"})
    first = await build_shed(STATIONS, _ctx(failed_router))
    exd = next(r for r in first if r["crs"] == "EXD")
    assert exd["routing_error"] == "failed"
    assert exd["kept"] is False

    # Resume: every non-failed record is done; EXD is re-routed.
    router2 = _CountingRouter()
    resumed = await build_shed(STATIONS, _ctx(router2), existing_records=first)
    assert ("EXD", "SW1V 2QQ") in router2.calls
    assert ("EXD", "EC3A 7LP") in router2.calls
    # All other stations were not re-routed.
    assert all(c[0] == "EXD" for c in router2.calls)
    exd2 = next(r for r in resumed if r["crs"] == "EXD")
    assert exd2["duration_pimlico"] == 150  # fresh successful route
    assert exd2["routing_error"] is None


@pytest.mark.asyncio
async def test_resume_prunes_station_removed_from_input():
    router = _CountingRouter()
    full = await build_shed(STATIONS, _ctx(router))
    # Existing records include a CRS that no longer exists in the station list.
    stale = [{
        "name": "Gone", "crs": "ZZZ", "lat": 52.0, "lon": 0.0,
        "duration_pimlico": 50, "duration_aldgate": 50, "kept": True,
    }]
    resumed = await build_shed(STATIONS, _ctx(router), existing_records=stale + full)
    assert all(r["crs"] != "ZZZ" for r in resumed)
    assert resumed == full


@pytest.mark.asyncio
async def test_resume_reroutes_station_with_changed_coords():
    router = _CountingRouter()
    full = await build_shed(STATIONS, _ctx(router))
    # Reading's coordinates changed in stations.csv — the old record is stale.
    moved = [
        Station("Reading", "RDG", 51.45, -0.96),  # different lat/lon than the original
        *STATIONS[1:],
    ]
    router2 = _CountingRouter()
    resumed = await build_shed(moved, _ctx(router2), existing_records=full)
    rdg = next(r for r in resumed if r["crs"] == "RDG")
    assert rdg["lat"] == 51.45 and rdg["lon"] == -0.96  # fresh record, new coords
    assert ("RDG", "SW1V 2QQ") in router2.calls  # was re-routed, not skipped
    assert len(router2.calls) == 2  # only RDG × 2 destinations
