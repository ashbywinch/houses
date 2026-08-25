"""Search set — turn shed + tiling into Rightmove search URLs (JSON + txt)."""

from __future__ import annotations

from tools.commute.rightmove_url import parse_search_url
from tools.commute.searches import (
    SearchOptions,
    build_searches,
    nearest_station_name,
    shed_to_searches,
    write_searches,
)
from tools.commute.station_shed import BBox
from tools.commute.tile import Rect

DESTINATIONS = ["SW1V 2QQ", "EC3A 7LP"]
KEPT_STATIONS = [
    {"name": "Reading", "crs": "RDG", "lat": 51.4599, "lon": -0.9705, "kept": True},
    {"name": "Guildford", "crs": "GLD", "lat": 51.2367, "lon": -0.5808, "kept": True},
]
RECTS = [
    Rect(lat_min=51.0, lat_max=51.1, lon_min=-1.0, lon_max=-0.9),
    Rect(lat_min=51.1, lat_max=51.2, lon_min=-1.0, lon_max=-0.9),
]
NOW = "2026-08-02T09:00:00+00:00"


def _payload(**overrides):
    kwargs = {
        "cell_km": 11.1,
        "buffer_km": 5.0,
        "threshold_min": 132,
        "destinations": DESTINATIONS,
        "min_beds": 2,
        "property_type": "houses",
        "generated_at": NOW,
        "engine_version": "v1",
    }
    kwargs.update(overrides)
    return build_searches(RECTS, KEPT_STATIONS, options=SearchOptions(**kwargs))


def test_build_searches_schema():
    payload = _payload()
    assert payload["metadata"]["count"] == 2
    s = payload["searches"][0]
    assert s["id"] == "s001"
    assert s["name"] == "Guildford area"  # nearest kept station to rect centre
    assert len(s["polygon"]) == 4
    assert s["filters"] == {"min_beds": 2, "property_type": "houses"}


def test_url_round_trips_to_polygon():
    s = _payload()["searches"][0]
    # The URL builder closes the loop: parsed == polygon + first point.
    assert parse_search_url(s["rightmove_url"]) == s["polygon"] + [s["polygon"][0]]


def test_url_carries_filters():
    url = _payload()["searches"][0]["rightmove_url"]
    assert "minBedrooms=2" in url
    assert "displayPropertyType=houses" in url


def test_deterministic_modulo_timestamp():
    a = _payload(generated_at="2026-08-02T09:00:00+00:00")
    b = _payload(generated_at="2026-08-03T09:00:00+00:00")
    assert a["searches"] == b["searches"]
    assert a["metadata"]["generated_at"] != b["metadata"]["generated_at"]


def test_ids_sequential():
    payload = _payload()
    assert [s["id"] for s in payload["searches"]] == ["s001", "s002"]


def test_nearest_station_name_picks_closest():
    rect = Rect(lat_min=51.4, lat_max=51.5, lon_min=-1.0, lon_max=-0.9)
    assert nearest_station_name(rect, KEPT_STATIONS) == "Reading area"


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_write_searches_does_not_churn_identical_content(tmp_path):
    from tools.commute.searches import _same_searches

    payload = _payload()
    write_searches(payload, tmp_path)
    first = (tmp_path / "searches.json").read_text()
    # Regenerate with only a new timestamp: nothing may be rewritten.
    refreshed = _payload(generated_at="2026-08-03T09:00:00+00:00")
    assert _same_searches(payload, refreshed) is True
    write_searches(refreshed, tmp_path)
    assert (tmp_path / "searches.json").read_text() == first
    # A real change to the searches must be written.
    changed = _payload()
    changed["searches"][0]["polygon"][0] = (51.05, -0.95)
    assert _same_searches(payload, changed) is False
    write_searches(changed, tmp_path)
    assert (tmp_path / "searches.json").read_text() != first


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_write_searches_regenerates_stale_txt(tmp_path):
    from tools.commute.searches import write_searches

    payload = _payload()
    write_searches(payload, tmp_path)
    json_first = (tmp_path / "searches.json").read_text()
    # Simulate a partial write: the txt disappears while the JSON stays.
    (tmp_path / "searches.txt").unlink()
    write_searches(payload, tmp_path)
    urls = (tmp_path / "searches.txt").read_text().splitlines()
    assert len(urls) == 2
    assert urls[0] == payload["searches"][0]["rightmove_url"]
    # The JSON was not rewritten (no churn).
    assert (tmp_path / "searches.json").read_text() == json_first


# ── end-to-end: shed records → searches ──────────────────────────────


SHED_RECORDS = [
    {"name": "Reading", "crs": "Rea", "lat": 51.4599, "lon": -0.9705,
     "duration_pimlico": 55, "duration_aldgate": 60, "kept": True},
    {"name": "Guildford", "crs": "Gui", "lat": 51.2367, "lon": -0.5808,
     "duration_pimlico": 70, "duration_aldgate": 75, "kept": True},
    {"name": "Woking", "crs": "Wok", "lat": 51.3173, "lon": -0.5571,
     "duration_pimlico": 60, "duration_aldgate": 65, "kept": True},
    {"name": "Exeter St Davids", "crs": "Exe", "lat": 50.7292, "lon": -3.5435,
     "duration_pimlico": 150, "duration_aldgate": 155, "kept": False},
]
BBOX = BBox(lat_min=50.1, lat_max=53.6, lon_min=-4.0, lon_max=2.0)


def test_shed_to_searches_covers_all_kept_stations():
    from houses.geopoint import GeoPoint
    from tools.commute.tile import point_to_rect_distance_km

    payload = shed_to_searches(
        SHED_RECORDS,
        BBOX,
        options=SearchOptions(
            cell_km=11.1,
            buffer_km=5.0,
            min_beds=2,
            property_type="houses",
            generated_at=NOW,
            engine_version="v1",
            threshold_min=132,
            destinations=DESTINATIONS,
        ),
    )
    searches = payload["searches"]
    assert len(searches) <= 100
    rects = [Rect(p[0][0], p[2][0], p[0][1], p[1][1]) for p in (s["polygon"] for s in searches)]
    for rec in SHED_RECORDS:
        if not rec["kept"]:
            continue
        point = GeoPoint(float(rec["lat"]), float(rec["lon"]))
        assert any(point_to_rect_distance_km(point, r) <= 5.0 + 1e-6 for r in rects), rec["name"]
    # Exeter is not kept — but may still sit inside a neighbouring cell; only
    # assert that its exclusion does not break the coverage of kept stations.


def test_shed_to_searches_rectangles_disjoint():
    from tools.commute.tile import Rect

    payload = shed_to_searches(
        SHED_RECORDS,
        BBOX,
        options=SearchOptions(
            cell_km=11.1,
            buffer_km=5.0,
            min_beds=2,
            property_type="houses",
            generated_at=NOW,
            engine_version="v1",
            threshold_min=132,
            destinations=DESTINATIONS,
        ),
    )

    def _polygon_to_rect(poly):
        lats = [p[0] for p in poly]
        lons = [p[1] for p in poly]
        return Rect(min(lats), max(lats), min(lons), max(lons))

    rects = [_polygon_to_rect(s["polygon"]) for s in payload["searches"]]
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            overlap = not (
                a.lat_max <= b.lat_min
                or b.lat_max <= a.lat_min
                or a.lon_max <= b.lon_min
                or b.lon_max <= a.lon_min
            )
            assert not overlap


def test_shed_to_searches_excludes_non_kept_station_cells():
    # A shed with ONLY Exeter (kept=False) produces no searches.
    records = [dict(SHED_RECORDS[3])]
    payload = shed_to_searches(
        records,
        BBOX,
        options=SearchOptions(
            cell_km=11.1,
            buffer_km=5.0,
            min_beds=2,
            property_type="houses",
            generated_at=NOW,
            engine_version="v1",
            threshold_min=132,
            destinations=DESTINATIONS,
        ),
    )
    assert payload["searches"] == []
