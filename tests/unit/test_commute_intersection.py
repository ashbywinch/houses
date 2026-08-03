"""Intersection — where every commute works: transit AND all drive sheds."""

from __future__ import annotations

import pytest

from tools.commute.intersection import (
    build_payload,
    common_grid,
    drive_cells,
    transit_cells,
    validate_payload,
)
from tools.commute.rightmove_url import parse_search_url
from tools.commute.tile import Grid
from tools.commute.units import KM

NOW = "2026-08-03T09:00:00+00:00"

SHED = {
    "metadata": {"threshold_min": 132},
    "stations": [
        {"name": "Centre", "crs": "CEN", "lat": 51.1, "lon": -0.9, "kept": True},
        {"name": "Far", "crs": "FAR", "lat": 51.6, "lon": -1.6, "kept": True},
        {"name": "Out", "crs": "OUT", "lat": 52.0, "lon": -2.0, "kept": False},
    ],
}
DRIVE_RAW = {
    "metadata": {"region_km": 50.0, "cell_km": 4.0},
    "destinations": [
        {"label": "Dad", "postcode": "OX7 5GZ", "lat": 51.0, "lon": -1.0, "threshold_min": 90,
            "cell_km": 4.0, "slack_min": 2.42, "grid": {}, "cells": []},
        {"label": "Bracknell", "postcode": "RG12 8YA", "lat": 51.2, "lon": -0.8, "threshold_min": 90,
            "cell_km": 4.0, "slack_min": 2.42, "grid": {}, "cells": []},
    ],
}
# Dad's shed: lat 50.8-51.4, lon -1.2..-0.6; Bracknell's: lat 50.8-51.3, lon -1.2..-0.7
DRIVE_SEARCHES = {
    "metadata": {"destinations": ["Dad", "Bracknell"]},
    "searches": [
        {
            "id": "drive-dad-090",
            "name": "Dad — 90 min drive",
            "polygon": [[50.8, -1.2], [50.8, -0.6], [51.4, -0.6], [51.4, -1.2]],
            "rightmove_url": "https://rm/dad",
            "destination": {"label": "Dad", "postcode": "OX7 5GZ", "lat": 51.0, "lon": -1.0},
            "threshold_min": 90,
        },
        {
            "id": "drive-bracknell-090",
            "name": "Bracknell — 90 min drive",
            "polygon": [[50.8, -1.2], [50.8, -0.7], [51.3, -0.7], [51.3, -1.2]],
            "rightmove_url": "https://rm/bracknell",
            "destination": {"label": "Bracknell", "postcode": "RG12 8YA", "lat": 51.2, "lon": -0.8},
            "threshold_min": 90,
        },
    ],
}


def _grid() -> Grid:
    return common_grid(DRIVE_RAW, 4.0 * KM)


def test_common_grid_is_the_overlap_of_drive_regions():
    import math

    grid = _grid()
    dad_lon = 50.0 / (111.0 * math.cos(math.radians(51.0)))
    brk_lon = 50.0 / (111.0 * math.cos(math.radians(51.2)))
    assert grid.bbox.lat_min == pytest.approx(51.2 - 50.0 / 111.0, abs=1e-9)
    assert grid.bbox.lat_max == pytest.approx(51.0 + 50.0 / 111.0, abs=1e-9)
    assert grid.bbox.lon_min == pytest.approx(max(-1.0 - dad_lon, -0.8 - brk_lon), abs=1e-9)
    assert grid.bbox.lon_max == pytest.approx(min(-1.0 + dad_lon, -0.8 + brk_lon), abs=1e-9)


def test_transit_cells_use_station_buffer_predicate():
    grid = _grid()
    kept = transit_cells([s for s in SHED["stations"] if s["kept"]], grid, 5.0 * KM)
    assert kept
    # the cell CONTAINING the Centre station is trivially within 5 km → kept
    assert (9, 10) in kept


def _all_centers(grid: Grid):
    from tools.commute.drive_isochrone import grid_cell_centers

    return grid_cell_centers(grid)


def test_drive_cells_are_cell_centers_inside_polygons():
    grid = _grid()
    dad = [s["polygon"] for s in DRIVE_SEARCHES["searches"] if s["destination"]["label"] == "Dad"]
    cells = drive_cells(dad, grid)
    assert cells
    inside = {(r, c) for r, c, lat, lon in _all_centers(grid) if 50.8 < lat < 51.4 and -1.2 < lon < -0.6}
    assert inside & cells == inside  # every centre inside the square is a member
    outside = {(r, c) for r, c, lat, lon in _all_centers(grid) if lat > 51.45}
    assert not (outside & cells)


def test_intersection_is_transit_and_every_drive_shed():
    payload = build_payload(
        shed=SHED,
        drive_raw=DRIVE_RAW,
        drive_searches=DRIVE_SEARCHES,
        generated_at=NOW,
    )
    assert payload["metadata"]["count"] == len(payload["searches"]) == 1
    poly = payload["searches"][0]["polygon"]
    # outline vertices sit at kept-cell CORNERS — up to a cell outside the
    # membership squares and the 5 km transit buffer; assert with margin
    for lat, lon in poly:
        assert 50.75 <= lat <= 51.35
        assert -1.25 <= lon <= -0.65
        assert ((lat - 51.1) * 111.0) ** 2 + ((lon + 0.9) * 68.5) ** 2 < 12.0**2
    # rounded to the encode precision and the URL round-trips
    assert all((round(lat, 5), round(lon, 5)) == (lat, lon) for lat, lon in poly)
    assert parse_search_url(payload["searches"][0]["rightmove_url"]) == poly + [poly[0]]


def test_payload_schema_and_filters():
    payload = build_payload(shed=SHED, drive_raw=DRIVE_RAW, drive_searches=DRIVE_SEARCHES, generated_at=NOW)
    s = payload["searches"][0]
    assert s["id"] == "intersection-090"
    assert s["name"] == "All commutes"
    assert s["filters"] == {"min_beds": 2, "property_type": "houses"}
    assert s["threshold_min"] == 90
    assert payload["metadata"]["engine_version"] == "intersection-v1"


def test_deterministic_modulo_timestamp():
    a = build_payload(shed=SHED, drive_raw=DRIVE_RAW, drive_searches=DRIVE_SEARCHES, generated_at=NOW)
    b = build_payload(
        shed=SHED, drive_raw=DRIVE_RAW, drive_searches=DRIVE_SEARCHES, generated_at="2026-08-03T10:00:00+00:00"
    )
    assert a["searches"] == b["searches"]


def test_validate_payload_passes_and_catches():
    payload = build_payload(shed=SHED, drive_raw=DRIVE_RAW, drive_searches=DRIVE_SEARCHES, generated_at=NOW)
    assert validate_payload(payload) == []
    payload["metadata"]["count"] = 99
    assert any("count" in i for i in validate_payload(payload))
    payload = build_payload(shed=SHED, drive_raw=DRIVE_RAW, drive_searches=DRIVE_SEARCHES, generated_at=NOW)
    payload["searches"][0]["polygon"][0] = (57.0, -1.0)
    assert any("bounding box" in i for i in validate_payload(payload))


def test_no_intersection_yields_empty_searches():
    shed = {"metadata": {}, "stations": [{"name": "Nowhere", "crs": "NOW", "lat": 55.0, "lon": -5.0, "kept": True}]}
    payload = build_payload(shed=shed, drive_raw=DRIVE_RAW, drive_searches=DRIVE_SEARCHES, generated_at=NOW)
    assert payload["searches"] == []
    assert payload["metadata"]["count"] == 0


def test_missing_destination_shed_makes_intersection_empty():
    """A destination with no shed (e.g. off the road network, or a low
    threshold) makes its constraint unsatisfiable — the intersection must be
    EMPTY, never silently dropped ("every commute works" would be a lie)."""
    dad_only = {
        "metadata": DRIVE_SEARCHES["metadata"],
        "searches": [s for s in DRIVE_SEARCHES["searches"] if s["destination"]["label"] == "Dad"],
    }
    payload = build_payload(shed=SHED, drive_raw=DRIVE_RAW, drive_searches=dad_only, generated_at=NOW)
    assert payload["searches"] == []
    assert payload["metadata"]["count"] == 0


def test_stale_drive_destination_raises():
    """drive_searches containing a destination absent from the config is
    stale committed data — ANDing it would silently narrow the intersection,
    so build_payload must reject it."""
    import copy

    stale_searches = copy.deepcopy(DRIVE_SEARCHES)
    stale_searches["searches"].append(
        {
            "id": "drive-ghost-090",
            "polygon": [[51.5, -1.5], [51.5, -1.4], [51.6, -1.4], [51.6, -1.5]],
            "rightmove_url": "https://rm/ghost",
            "destination": {"label": "Ghost", "postcode": "AA1 1AA", "lat": 51.55, "lon": -1.45},
            "threshold_min": 90,
        }
    )
    with pytest.raises(ValueError, match="stale drive data"):
        build_payload(shed=SHED, drive_raw=DRIVE_RAW, drive_searches=stale_searches, generated_at=NOW)


def test_common_grid_rejects_no_destinations():
    """Regression: max() over an empty destination list used to crash with a
    bare ValueError on empty input."""
    import copy

    empty = copy.deepcopy(DRIVE_RAW)
    empty["destinations"] = []
    with pytest.raises(ValueError, match="no drive destinations"):
        common_grid(empty)


async def test_run_fails_cleanly_with_no_drive_destinations(tmp_path):
    """run() must exit with the two-tier message, not a traceback, when the
    drive payload has no destinations."""
    import json

    from tools.commute.intersection import run as intersection_run

    shed = {"metadata": {}, "stations": [{"name": "S", "crs": "S", "lat": 51.1, "lon": -0.9, "kept": True}]}
    drive_raw = {"metadata": {"region_km": 50.0, "threshold_min": 90}, "destinations": []}
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    shed_path = tmp_path / "shed.json"
    shed_path.write_text(json.dumps(shed))
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(json.dumps(drive_raw))
    searches_path = tmp_path / "searches.json"
    searches_path.write_text(json.dumps({"metadata": {}, "searches": []}))

    code = intersection_run(
        [
            "--shed", str(shed_path),
            "--drive-raw", str(raw_path),
            "--drive-searches", str(searches_path),
            "--out", str(out_dir / "intersection.json"),
        ]
    )
    assert code == 1
    assert not (out_dir / "intersection.json").exists()


def test_missing_inputs_reported_in_one_pass(tmp_path, capsys):
    """With all three inputs missing, the user gets the complete fix list in
    one run — not one file per invocation."""
    from tools.commute.intersection import run as intersection_run

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    code = intersection_run(
        [
            "--shed", str(tmp_path / "nope_shed.json"),
            "--drive-raw", str(tmp_path / "nope_raw.json"),
            "--drive-searches", str(tmp_path / "nope_searches.json"),
            "--out", str(out_dir / "intersection.json"),
        ]
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "commute-shed" in err
    assert "commute-drive" in err


def test_validate_fails_cleanly_on_list_shaped_intersection(tmp_path):
    """--validate with a top-level-list intersection.json must exit with
    the two-tier message, not an AttributeError."""
    from tools.commute.intersection import run as intersection_run

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "intersection.json").write_text("[1, 2, 3]")
    code = intersection_run(["--out", str(out_dir / "intersection.json"), "--validate"])
    assert code == 1


def test_validate_fails_cleanly_on_corrupt_intersection(tmp_path):
    """--validate with a truncated intersection.json must exit with the
    two-tier message, not a JSONDecodeError traceback."""
    from tools.commute.intersection import run as intersection_run

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "intersection.json").write_text("{ nope")
    code = intersection_run(["--out", str(out_dir / "intersection.json"), "--validate"])
    assert code == 1


def test_run_fails_cleanly_on_malformed_shed(tmp_path):
    """A shed file that is valid JSON but missing the 'stations' key must
    exit with the two-tier message, not a KeyError traceback."""
    import json as _json

    from tools.commute.intersection import run as intersection_run

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    shed_path = tmp_path / "shed.json"
    shed_path.write_text(_json.dumps({"metadata": {}, "unexpected": True}))
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(_json.dumps({"metadata": {"region_km": 50.0}, "destinations": []}))
    searches_path = tmp_path / "searches.json"
    searches_path.write_text(_json.dumps({"metadata": {}, "searches": []}))

    code = intersection_run(
        [
            "--shed", str(shed_path),
            "--drive-raw", str(raw_path),
            "--drive-searches", str(searches_path),
            "--out", str(out_dir / "intersection.json"),
        ]
    )
    assert code == 1
    assert not (out_dir / "intersection.json").exists()
