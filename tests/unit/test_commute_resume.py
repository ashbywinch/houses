"""Resume/checkpoint behaviour — a killed batch must not redo completed work."""

from __future__ import annotations

import pytest

from houses.geo import GeoPoint
from tools.commute.station_shed import BBox, Office, Station, build_shed

PIMLICO = GeoPoint(51.4904, -0.1378)
ALDGATE = GeoPoint(51.5145, -0.0762)
OFFICES = [Office("SW1V 2QQ", PIMLICO), Office("EC3A 7LP", ALDGATE)]
BBOX = BBox(lat_min=50.0, lat_max=54.0, lon_min=-4.0, lon_max=2.0)
THRESHOLD = 132

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
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, station: Station, dest: str) -> int | None:
        self.calls.append((station.crs, dest))
        return DURATIONS.get((station.crs, dest))


@pytest.mark.asyncio
async def test_resume_skips_stations_already_in_checkpoint():
    router = _CountingRouter()
    # First run processes everything.
    full = await build_shed(STATIONS, OFFICES, BBOX, 20.0, THRESHOLD, router, delay_s=0)
    assert len(router.calls) == 8  # 4 routed stations × 2 destinations (EAL inner)

    # Second run with the first two records as the checkpoint: the checkpointed
    # stations are never re-routed; only the remaining ones are.
    router.calls.clear()
    resumed = await build_shed(
        STATIONS, OFFICES, BBOX, 20.0, THRESHOLD, router, delay_s=0, existing_records=full[:2]
    )
    assert all(c[0] in {"WOK", "EXD"} for c in router.calls)  # RDG/GLD/EAL not re-routed
    assert len(router.calls) == 4  # Woking + Exeter × 2 destinations
    assert resumed == full  # and the outcome is identical to a from-scratch run


@pytest.mark.asyncio
async def test_resume_routes_only_remaining_stations():
    router = _CountingRouter()
    full = await build_shed(STATIONS, OFFICES, BBOX, 20.0, THRESHOLD, router, delay_s=0)
    checkpoint = full[:3]  # Reading, Guildford, Woking done

    router2 = _CountingRouter()
    resumed = await build_shed(
        STATIONS, OFFICES, BBOX, 20.0, THRESHOLD, router2, delay_s=0, existing_records=checkpoint
    )
    # Only Exeter (EXD × 2 destinations) is routed; EAL is inner-zone (kept, no calls).
    assert sorted(router2.calls) == [("EXD", "EC3A 7LP"), ("EXD", "SW1V 2QQ")]
    assert resumed == full


@pytest.mark.asyncio
async def test_resume_when_checkpoint_complete_makes_no_calls():
    router = _CountingRouter()
    full = await build_shed(STATIONS, OFFICES, BBOX, 20.0, THRESHOLD, router, delay_s=0)
    router.calls.clear()
    resumed = await build_shed(
        STATIONS, OFFICES, BBOX, 20.0, THRESHOLD, router, delay_s=0, existing_records=full
    )
    assert router.calls == []
    assert resumed == full
