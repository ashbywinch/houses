"""Validation — geometry, coverage, and URL checks for the search set."""

from __future__ import annotations

from tools.commute.searches import build_searches, shed_to_searches
from tools.commute.station_shed import BBox
from tools.commute.tile import Rect
from tools.commute.validate import validate

DESTINATIONS = ["SW1V 2QQ", "EC3A 7LP"]
NOW = "2026-08-02T09:00:00+00:00"
BBOX = BBox(lat_min=50.1, lat_max=53.6, lon_min=-4.0, lon_max=2.0)

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


def _payload():
    return shed_to_searches(
        SHED_RECORDS,
        BBOX,
        cell_km=11.1,
        buffer_km=5.0,
        min_beds=2,
        property_type="houses",
        generated_at=NOW,
        engine_version="v1",
        threshold_min=132,
        destinations=DESTINATIONS,
    )


KEPT = [r for r in SHED_RECORDS if r["kept"]]


def test_validate_clean_payload_passes():
    issues = validate(
        _payload(),
        KEPT,
        buffer_km=5.0,
        bbox=BBOX,
        positive=[("Reading", 51.4599, -0.9705), ("Guildford", 51.2367, -0.5808)],
        negative=[("Exeter St Davids", 50.7292, -3.5435)],
    )
    assert issues == []


def test_validate_positive_coverage_failure():
    issues = validate(
        _payload(),
        KEPT,
        buffer_km=5.0,
        bbox=BBOX,
        positive=[("Brighton", 50.8290, -0.1410)],
        negative=[],
    )
    assert any("Brighton" in i for i in issues)


def test_validate_negative_control_failure():
    issues = validate(
        _payload(),
        KEPT,
        buffer_km=5.0,
        bbox=BBOX,
        positive=[],
        negative=[("Reading", 51.4599, -0.9705)],  # Reading IS covered — must fail
    )
    assert any("Reading" in i for i in issues)


def test_validate_rectangle_count_failure():
    issues = validate(_payload(), KEPT, buffer_km=5.0, bbox=BBOX, positive=[], negative=[], max_rectangles=1)
    assert any("rectangles" in i for i in issues)


def test_validate_url_roundtrip_failure():
    payload = _payload()
    # Tamper the stored polygon after the URL was built: the URL no longer
    # decodes back to the JSON polygon.
    payload["searches"][0]["polygon"][0] = (51.0, -1.0)
    issues = validate(payload, KEPT, buffer_km=5.0, bbox=BBOX, positive=[], negative=[])
    assert any("round-trip" in i for i in issues)


def test_validate_geometry_bbox_failure():
    payload = _payload()
    # A polygon with a corner outside the bbox must be flagged.
    payload["searches"][0]["polygon"][0] = (54.0, -0.5)  # north of bbox
    issues = validate(payload, KEPT, buffer_km=5.0, bbox=BBOX, positive=[], negative=[])
    assert any("outside" in i for i in issues)


def test_validate_kept_station_coverage_failure():
    # A search set built from rects that omit one kept station.
    kept = [{"name": "Reading", "crs": "RDG", "lat": 51.4599, "lon": -0.9705, "kept": True}]
    rect = Rect(lat_min=51.4, lat_max=51.5, lon_min=-1.0, lon_max=-0.9)  # near Reading — covered
    payload = build_searches(
        [rect],
        kept,
        threshold_min=132,
        destinations=DESTINATIONS,
        min_beds=2,
        property_type="houses",
        generated_at=NOW,
        engine_version="v1",
    )
    far_kept = [{"name": "Brighton", "crs": "BTN", "lat": 50.8290, "lon": -0.1410, "kept": True}]
    issues = validate(payload, far_kept, buffer_km=5.0, bbox=BBOX, positive=[], negative=[])
    assert any("Brighton" in i for i in issues)


def test_validate_disjointness_failure():
    payload = _payload()
    # Overlap two searches by replacing the second polygon with the first's.
    payload["searches"][1]["polygon"] = payload["searches"][0]["polygon"]
    issues = validate(payload, KEPT, buffer_km=5.0, bbox=BBOX, positive=[], negative=[])
    assert any("overlap" in i for i in issues)
