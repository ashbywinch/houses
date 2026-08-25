"""Drive isochrones — one-off ORS matrix batch producing driving search URLs."""

from __future__ import annotations

import json
import math

import pytest

from houses.geopoint import GeoPoint
from tools.commute.drive_isochrone import (
    DEFAULT_THRESHOLD_MIN,
    DriveDestination,
    _components,
    _signed_area,
    build_matrix_requests,
    grid_cell_centers,
    kept_cells,
    load_config,
    parse_durations,
    point_in_polygon,
    raw_to_searches,
    region_bbox,
    slack_minutes,
    validate_payload,
)
from tools.commute.rightmove_url import parse_search_url
from tools.commute.tile import Grid, GridCell, Rect
from tools.commute.units import KM, MINUTE

NOW = "2026-08-03T09:00:00+00:00"


async def _fake_geocode(postcode: str) -> GeoPoint:
    """Fixed coords per postcode — the synthetic raws store these, so the
    offline reuse path matches (DI, not monkeypatching)."""
    return {"OX7 5GZ": GeoPoint(51.1, -1.88), "RG12 8YA": GeoPoint(51.1, -1.88)}.get(
        postcode, GeoPoint(51.1, -1.88)
    )


def _grid(
    rows: int, cols: int, lat0: float = 51.0, lon0: float = -2.0, lat_deg: float = 0.05, lon_deg: float = 0.08
) -> Grid:
    return Grid(bbox=Rect(lat0, lat0 + rows * lat_deg, lon0, lon0 + cols * lon_deg), lat_deg=lat_deg, lon_deg=lon_deg)


def _dest(label: str = "Dad", postcode: str = "OX7 5GZ", threshold: int = 90) -> DriveDestination:
    return DriveDestination(label=label, postcode=postcode, threshold_min=threshold * MINUTE)


# ── config ───────────────────────────────────────────────────────────


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_load_config_applies_global_threshold(tmp_path):
    cfg = tmp_path / "destinations.json"
    cfg.write_text(json.dumps({"threshold_min": 90, "destinations": [{"label": "Dad", "postcode": "OX7 5GZ"}]}))
    assert load_config(cfg) == [_dest(threshold=90)]


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_load_config_default_threshold_when_omitted(tmp_path):
    cfg = tmp_path / "destinations.json"
    cfg.write_text(json.dumps({"destinations": [{"label": "Dad", "postcode": "OX7 5GZ"}]}))
    assert load_config(cfg) == [_dest(threshold=DEFAULT_THRESHOLD_MIN)]


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_load_config_default_threshold_parameter(tmp_path):
    """The --threshold-min flag feeds this parameter; destinations without an
    explicit override adopt it (per-destination overrides still win)."""
    cfg = tmp_path / "destinations.json"
    body = {
        "destinations": [
            {"label": "Dad", "postcode": "OX7 5GZ"},
            {"label": "Bracknell", "postcode": "RG12 8YA", "threshold_min": 75},
        ]
    }
    cfg.write_text(json.dumps(body))
    assert load_config(cfg, default_threshold=60) == [
        _dest(threshold=60),
        _dest(label="Bracknell", postcode="RG12 8YA", threshold=75),
    ]


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_apply_default_threshold_skips_explicit_overrides(tmp_path):
    """Regression: the CLI flag must beat the config file's TOP-LEVEL
    threshold_min (load_config's default_threshold cannot — the file wins),
    but per-destination overrides in the file still win."""
    import json as _json

    from tools.commute.drive_isochrone import apply_default_threshold

    cfg = tmp_path / "destinations.json"
    cfg.write_text(
        _json.dumps(
            {
                "threshold_min": 90,
                "destinations": [
                    {"label": "Dad", "postcode": "OX7 5GZ"},
                    {"label": "Bracknell", "postcode": "RG12 8YA", "threshold_min": 75},
                ],
            }
        )
    )
    data = _json.loads(cfg.read_text())
    dests = apply_default_threshold(load_config(cfg), data, 60 * MINUTE)
    assert [d.threshold_min.to("minute").magnitude for d in dests] == [60, 75]


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_load_config_per_destination_override_wins(tmp_path):
    cfg = tmp_path / "destinations.json"
    body = {"threshold_min": 90, "destinations": [{"label": "Dad", "postcode": "OX7 5GZ", "threshold_min": 120}]}
    cfg.write_text(json.dumps(body))
    assert load_config(cfg) == [_dest(threshold=120)]


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_load_config_rejects_duplicate_labels(tmp_path):
    """Duplicate labels collapse two destinations into one constraint in the
    intersection (OR instead of AND) — rejected at the config layer."""
    cfg = tmp_path / "destinations.json"
    cfg.write_text(
        json.dumps(
            {
                "destinations": [
                    {"label": "Dad", "postcode": "OX7 5GZ"},
                    {"label": "Dad", "postcode": "RG12 8YA"},
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="duplicate destination labels"):
        load_config(cfg)


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_load_config_rejects_missing_fields(tmp_path):
    cfg = tmp_path / "destinations.json"
    cfg.write_text(json.dumps({"destinations": [{"postcode": "OX7 5GZ"}]}))
    with pytest.raises(ValueError):
        load_config(cfg)
    cfg.write_text(json.dumps({"destinations": [{"label": "Dad"}]}))
    with pytest.raises(ValueError):
        load_config(cfg)


# ── geometry helpers ─────────────────────────────────────────────────


def test_slack_minutes_is_half_diagonal_crossing_time():
    # 4 km cell, half-diagonal 2.83 km, at 70 km/h → 2.42 min
    slack = slack_minutes(4.0 * KM)
    assert slack.to("minute").magnitude == pytest.approx(4.0 * math.sqrt(2) / 2 / 70.0 * 60.0, abs=1e-9)


def test_region_bbox_centers_on_destination():
    rect = region_bbox(51.94, -1.55, 100.0 * KM)
    assert (rect.lat_min + rect.lat_max) / 2 == pytest.approx(51.94)
    assert (rect.lon_min + rect.lon_max) / 2 == pytest.approx(-1.55)
    assert rect.lat_max - rect.lat_min == pytest.approx(2 * 100.0 / 111.0, rel=0.01)


def test_grid_cell_centers_row_major():
    grid = _grid(rows=2, cols=3)
    cells = grid_cell_centers(grid)
    assert len(cells) == 6
    # first cell is the south-west-most; rows then columns sweep north/east
    first = cells[0]
    assert (first.row, first.col) == (0, 0)
    assert first.lat == pytest.approx(51.0 + 0.05 / 2)
    assert first.lon == pytest.approx(-2.0 + 0.08 / 2)
    assert (cells[1].row, cells[1].col) == (0, 1)
    assert cells[1].lat == pytest.approx(51.025) and cells[1].lon == pytest.approx(-1.88)
    assert (cells[3].row, cells[3].col) == (1, 0)
    assert cells[3].lat == pytest.approx(51.075) and cells[3].lon == pytest.approx(-1.96)


# ── ORS matrix requests ──────────────────────────────────────────────


def test_build_matrix_requests_single_chunk():
    centers = [(-1.9, 51.1), (-1.8, 51.2), (-1.7, 51.3)]  # (lon, lat) — ORS location order
    bodies = build_matrix_requests(-1.55, 51.94, centers, max_locations=100)
    assert len(bodies) == 1
    body = bodies[0]
    assert body["locations"][0] == [-1.55, 51.94]  # destination first, lon/lat
    assert body["locations"][1:] == centers
    assert body["sources"] == [1, 2, 3]
    assert body["destinations"] == [0]
    assert body["metrics"] == ["duration"]


def test_build_matrix_requests_chunks_at_location_cap():
    centers = [(51.1 + i * 0.1, -1.9) for i in range(5)]
    bodies = build_matrix_requests(-1.55, 51.94, centers, max_locations=3)  # dest + 2 centers each
    assert [len(b["locations"]) for b in bodies] == [3, 3, 2]
    assert len([s for b in bodies for s in b["sources"]]) == 5
    assert [b["sources"] for b in bodies] == [[1, 2], [1, 2], [1]]
    assert all(b["destinations"] == [0] for b in bodies)


def test_parse_durations_seconds_to_minutes_preserves_nulls():
    data = {"durations": [[3090.57], [None], [0.0]]}
    out = parse_durations(data, 3)
    assert out[0] is not None and out[0].to("minute").magnitude == pytest.approx(51.5095)
    assert out[1] is None
    assert out[2] is not None and out[2].to("minute").magnitude == 0.0


def test_config_signature_fingerprints_coordinates():
    """The reuse guard must include the geocoded coordinates: a postcode
    whose geocoding changed must regenerate, not silently reuse the old
    grid centred on the old location."""
    import json as _json

    from tools.commute.drive_isochrone import config_signature

    raw = _json.loads(
        _json.dumps(
            {
                "metadata": {
                    "engine_version": "drive-isochrone-v1",
                    "profile": "driving-car",
                    "threshold_min": 90,
                    "cell_km": 4.0,
                    "region_km": 153.0,
                    "destinations": [{"label": "Dad", "postcode": "OX7 5GZ", "threshold_min": 90}],
                },
                "destinations": [{"label": "Dad", "postcode": "OX7 5GZ", "lat": 51.1, "lon": -1.88, "cells": []}],
            }
        )
    )
    assert config_signature(raw)["coords"] == {"Dad": (51.1, -1.88)}


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
async def test_changed_geocode_rejects_raw_reuse(tmp_path, capsys):
    """A raw built around the OLD geocoded coordinates must not be reused
    when the postcode now geocodes elsewhere."""
    import json as _json

    from tools.commute.drive_isochrone import run as drive_run

    cfg = tmp_path / "destinations.json"
    cfg.write_text(_json.dumps({"threshold_min": 90, "destinations": [{"label": "Dad", "postcode": "OX7 5GZ"}]}))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    raw = {
        "metadata": {
            "engine_version": "drive-isochrone-v1",
            "profile": "driving-car",
            "speed_model": "free-flow",
            "threshold_min": 90,
            "cell_km": 4.0,
            "region_km": 153.0,
            "destinations": [{"label": "Dad", "postcode": "OX7 5GZ", "threshold_min": 90}],
        },
        "destinations": [{"label": "Dad", "postcode": "OX7 5GZ", "lat": 51.1, "lon": -1.88, "threshold_min": 90,
                          "cell_km": 4.0, "slack_min": 2.42, "grid": {}, "cells": []}],
    }
    (out_dir / "drive_isochrone.json").write_text(_json.dumps(raw))

    async def _moved_geocode(postcode):  # geocoder now resolves elsewhere
        return GeoPoint(51.5, -2.0)

    code = await drive_run(
        ["--config", str(cfg), "--out-dir", str(out_dir)], geocoder=_moved_geocode, ors_key=""
    )
    assert code == 1  # no ORS key → the regenerate path fails cleanly
    assert "regenerating" in capsys.readouterr().err


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
async def test_transient_geocode_failure_still_reuses_matching_raw(tmp_path, capsys):
    """A transient httpx geocoding failure (429/5xx) must also defer to the
    offline reuse path, not crash with a raw traceback."""
    import json as _json

    import httpx

    from tools.commute.drive_isochrone import run as drive_run

    cfg = tmp_path / "destinations.json"
    cfg.write_text(_json.dumps({"threshold_min": 90, "destinations": [{"label": "Dad", "postcode": "OX7 5GZ"}]}))
    grid = {"lat_min": 51.0, "lat_max": 51.2, "lon_min": -2.0, "lon_max": -1.76}
    cells = [
        {"r": r, "c": c, "lat": 51.0 + (r + 0.5) * 0.05, "lon": -2.0 + (c + 0.5) * 0.08, "duration_min": 10.0}
        for r in range(2)
        for c in range(2)
    ]
    raw = {
        "metadata": {
            "engine_version": "drive-isochrone-v1", "profile": "driving-car", "speed_model": "free-flow",
            "threshold_min": 90, "cell_km": 4.0, "region_km": 153.0,
            "destinations": [{"label": "Dad", "postcode": "OX7 5GZ", "threshold_min": 90}],
            "generated_at": NOW, "count": 1,
        },
        "destinations": [
            {"label": "Dad", "postcode": "OX7 5GZ", "lat": 51.03, "lon": -1.95, "threshold_min": 90,
             "cell_km": 4.0, "slack_min": 2.42, "grid": grid, "cells": cells}
        ],
    }
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "drive_isochrone.json").write_text(_json.dumps(raw))

    async def _throttled_geocode(postcode):
        raise httpx.HTTPStatusError(
            "429", request=httpx.Request("GET", "https://geocode"), response=httpx.Response(429)
        )

    code = await drive_run(["--config", str(cfg), "--out-dir", str(out_dir)], geocoder=_throttled_geocode)
    assert code == 0
    assert "reusing" in capsys.readouterr().out


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
async def test_geocode_failure_still_reuses_matching_raw(tmp_path, capsys):
    """A geocoding outage must not block offline reuse of a matching
    committed raw — the stored coordinates drive the signature comparison."""
    import json as _json

    from tools.commute.drive_isochrone import run as drive_run

    cfg = tmp_path / "destinations.json"
    cfg.write_text(_json.dumps({"threshold_min": 90, "destinations": [{"label": "Dad", "postcode": "OX7 5GZ"}]}))
    grid = {"lat_min": 51.0, "lat_max": 51.2, "lon_min": -2.0, "lon_max": -1.76}
    cells = [
        {"r": r, "c": c, "lat": 51.0 + (r + 0.5) * 0.05, "lon": -2.0 + (c + 0.5) * 0.08, "duration_min": 10.0}
        for r in range(2)
        for c in range(2)
    ]
    raw = {
        "metadata": {
            "engine_version": "drive-isochrone-v1", "profile": "driving-car", "speed_model": "free-flow",
            "threshold_min": 90, "cell_km": 4.0, "region_km": 153.0,
            "destinations": [{"label": "Dad", "postcode": "OX7 5GZ", "threshold_min": 90}],
            "generated_at": NOW, "count": 1,
        },
        "destinations": [
            {"label": "Dad", "postcode": "OX7 5GZ", "lat": 51.03, "lon": -1.95, "threshold_min": 90,
             "cell_km": 4.0, "slack_min": 2.42, "grid": grid, "cells": cells}
        ],
    }
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "drive_isochrone.json").write_text(_json.dumps(raw))

    async def _down_geocode(postcode):
        raise RuntimeError("geocoder down")

    code = await drive_run(["--config", str(cfg), "--out-dir", str(out_dir)], geocoder=_down_geocode)
    assert code == 0
    assert "reusing" in capsys.readouterr().out
    assert (out_dir / "drive_searches.json").exists()


def test_parse_durations_wrong_row_count_raises():
    with pytest.raises(ValueError):
        parse_durations({"durations": [[1.0]]}, 2)


def test_drive_map_html_escapes_user_labels():
    """Regression: destination labels are user-controlled — a label with
    </script> must not break out of the drive map's script element or inject
    into the marker popup."""
    from tools.commute.drive_isochrone import _map_html

    evil = "</script><script>alert(1)</script>"
    searches = {
        "metadata": {},
        "searches": [
            {
                "id": "x",
                "polygon": [[51.0, -1.0], [51.0, -0.9], [51.1, -0.9], [51.1, -1.0]],
                "rightmove_url": "https://rm/x",
                "destination": {"label": evil, "lat": 51.05, "lon": -0.95},
            }
        ],
    }
    html = _map_html(searches)
    assert "</script><script>" not in html
    assert "\\u0026lt;script\\u0026gt;alert(1)" in html  # escaped form present


def test_drive_map_html_one_marker_per_destination_label():
    """Regression: a shed that splits into components produces one search
    record per component at the SAME coordinates — the standalone map must
    render one marker per destination label, not stacked duplicates."""
    from tools.commute.drive_isochrone import _map_html

    searches = {
        "metadata": {},
        "searches": [
            {
                "id": "drive-dad-ox75gz-090",
                "polygon": [[51.0, -1.0], [51.0, -0.9], [51.1, -0.9], [51.1, -1.0]],
                "rightmove_url": "https://rm/a",
                "destination": {"label": "Dad", "lat": 51.05, "lon": -0.95},
            },
            {
                "id": "drive-dad-ox75gz-090-2",
                "polygon": [[52.0, -1.0], [52.0, -0.9], [52.1, -0.9], [52.1, -1.0]],
                "rightmove_url": "https://rm/b",
                "destination": {"label": "Dad", "lat": 51.05, "lon": -0.95},
            },
        ],
    }
    html = _map_html(searches)
    assert html.count('"label": "Dad"') == 1
    assert "https://rm/a" in html  # first record's URL wins
    assert "https://rm/b" not in html


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
async def test_run_fails_cleanly_on_bad_config(tmp_path):
    """A missing/corrupt destinations config must exit with the two-tier
    message, not a bare traceback."""
    from tools.commute.drive_isochrone import run as drive_run

    bad_cfg = tmp_path / "destinations.json"
    bad_cfg.write_text("{ not json")
    code = await drive_run(["--config", str(bad_cfg), "--out-dir", str(tmp_path / "out")])
    assert code == 1


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
async def test_run_fails_cleanly_on_wrong_shaped_config(tmp_path):
    """A config file that is valid JSON but the wrong shape (a list, not an
    object) must exit via the two-tier message, not an AttributeError at
    data.get."""
    import json

    from tools.commute.drive_isochrone import run as drive_run

    cfg = tmp_path / "destinations.json"
    cfg.write_text(json.dumps([1, 2, 3]))
    code = await drive_run(["--config", str(cfg), "--out-dir", str(tmp_path / "out")])
    assert code == 1


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
async def test_run_fails_cleanly_on_empty_destinations_config(tmp_path):
    """An empty destinations list is accepted by the JSON shape but means
    nothing to route — exit with the two-tier message, not a min() over
    empty traceback."""
    import json

    from tools.commute.drive_isochrone import run as drive_run

    cfg = tmp_path / "destinations.json"
    cfg.write_text(json.dumps({"threshold_min": 90, "destinations": []}))
    code = await drive_run(["--config", str(cfg), "--out-dir", str(tmp_path / "out")])
    assert code == 1


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
async def test_validate_fails_cleanly_on_dict_shaped_searches(tmp_path):
    """--validate with a dict-shaped "searches" value must exit with the
    two-tier message, not iterate dict keys and crash."""
    import json as _json

    from tools.commute.drive_isochrone import run as drive_run

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "drive_searches.json").write_text(_json.dumps({"searches": {"a": 1}}))
    code = await drive_run(["--out-dir", str(out_dir), "--validate"])
    assert code == 1


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
async def test_validate_fails_cleanly_on_missing_searches_key(tmp_path):
    """A payload without a 'searches' key must be flagged, not pass
    validation and then crash the success print with a KeyError."""
    import json as _json

    from tools.commute.drive_isochrone import run as drive_run

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "drive_searches.json").write_text(_json.dumps({"metadata": {"count": 0, "destinations": []}}))
    code = await drive_run(["--out-dir", str(out_dir), "--validate"])
    assert code == 1


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
async def test_validate_fails_cleanly_on_null_metadata(tmp_path):
    """--validate with "metadata": null must exit with the two-tier message,
    not an AttributeError at metadata.get."""
    import json as _json

    from tools.commute.drive_isochrone import run as drive_run

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "drive_searches.json").write_text(_json.dumps({"metadata": None, "searches": []}))
    code = await drive_run(["--out-dir", str(out_dir), "--validate"])
    assert code == 1


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
async def test_validate_fails_cleanly_on_list_shaped_searches(tmp_path):
    """--validate with a top-level-list drive_searches.json must exit with
    the two-tier message, not an AttributeError at .get("searches")."""
    from tools.commute.drive_isochrone import run as drive_run

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "drive_searches.json").write_text("[1, 2, 3]")
    code = await drive_run(["--out-dir", str(out_dir), "--validate"])
    assert code == 1


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
async def test_validate_fails_cleanly_on_corrupt_searches(tmp_path):
    """--validate with a corrupt drive_searches.json must not traceback."""
    from tools.commute.drive_isochrone import run as drive_run

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "drive_searches.json").write_text("{ nope")
    code = await drive_run(["--out-dir", str(out_dir), "--validate"])
    assert code == 1


async def test_fetch_matrix_does_not_retry_non_transient():
    """A 401/400 can never succeed on retry — fail fast with one call, no
    2s stall and no wasted request. DI (client kwarg), not monkeypatching."""
    import httpx

    from tools.commute.drive_isochrone import fetch_matrix

    calls = {"n": 0}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            calls["n"] += 1
            return httpx.Response(401, request=httpx.Request("POST", url))

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_matrix({"locations": []}, key="k", client=FakeClient())
    assert calls["n"] == 1


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
async def test_run_fails_cleanly_on_malformed_raw_payload(tmp_path):
    """A structurally-broken committed raw payload (valid JSON, missing
    metadata) must exit via the two-tier message, not a KeyError traceback
    in the offline reuse path."""
    import json

    from tools.commute.drive_isochrone import run as drive_run

    cfg = tmp_path / "destinations.json"
    cfg.write_text(
        json.dumps(
            {
                "threshold_min": 90,
                "destinations": [{"label": "Dad", "postcode": "OX7 5GZ"}],
            }
        )
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "drive_isochrone.json").write_text(json.dumps({"destinations": []}))  # no metadata

    code = await drive_run(["--config", str(cfg), "--out-dir", str(out_dir)], geocoder=_fake_geocode)
    assert code == 1
    assert not (out_dir / "drive_searches.json").exists()


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
async def test_run_does_not_write_when_validation_fails(tmp_path):
    """Regression: run() wrote the searches BEFORE validating, so a failing
    payload (e.g. a destination whose shed vanished) left an invalid committed
    artifact on disk. Validation must gate the write.

    Self-contained: a synthetic raw payload whose metadata matches the (also
    synthetic) config, so run() takes the offline reuse path — no geocoding,
    no API calls, no coupling to committed data.
    """
    import json

    from tools.commute.drive_isochrone import run as drive_run

    cfg = tmp_path / "destinations.json"
    cfg.write_text(
        json.dumps(
            {
                "threshold_min": 90,
                "destinations": [
                    {"label": "Dad", "postcode": "OX7 5GZ"},
                    {"label": "Bracknell", "postcode": "RG12 8YA"},
                ],
            }
        )
    )
    grid = {"lat_min": 51.0, "lat_max": 51.2, "lon_min": -2.0, "lon_max": -1.76}
    cells = [
        {"r": r, "c": c, "lat": 51.0 + (r + 0.5) * 0.05, "lon": -2.0 + (c + 0.5) * 0.08, "duration_min": 10.0}
        for r in range(2)
        for c in range(2)
    ]
    raw = {
        "metadata": {
            "engine_version": "drive-isochrone-v1",
            "profile": "driving-car",
            "speed_model": "free-flow",
            "threshold_min": 90,
            "cell_km": 4.0,
            "region_km": 153.0,
            "destinations": [
                {"label": "Dad", "postcode": "OX7 5GZ", "threshold_min": 90},
                {"label": "Bracknell", "postcode": "RG12 8YA", "threshold_min": 90},
            ],
            "generated_at": NOW,
            "count": 2,
        },
        "destinations": [
            {
                "label": "Dad", "postcode": "OX7 5GZ", "lat": 51.1, "lon": -1.88, "threshold_min": 90,
                "cell_km": 4.0, "slack_min": 2.42, "grid": grid, "cells": cells,
            },
            # Bracknell's shed is EMPTY: raw_to_searches emits no Bracknell
            # search and validate_payload reports the lost destination
            {
                "label": "Bracknell", "postcode": "RG12 8YA", "lat": 51.1, "lon": -1.88, "threshold_min": 90,
                "cell_km": 4.0, "slack_min": 2.42, "grid": grid, "cells": [],
            },
        ],
    }
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "drive_isochrone.json").write_text(json.dumps(raw))

    code = await drive_run(["--config", str(cfg), "--out-dir", str(out_dir)], geocoder=_fake_geocode)
    assert code == 1
    assert not (out_dir / "drive_searches.json").exists()
    assert not (out_dir / "drive_searches.txt").exists()


# ── kept cells ───────────────────────────────────────────────────────


def test_kept_cells_threshold_boundary():
    cells = [GridCell(r, c, 51.0 + r * 0.05, -2.0 + c * 0.08) for r in range(2) for c in range(3)]
    durations = [d * MINUTE if d is not None else None for d in (0.0, 89.0, 90.0, 90.5, 91.0, None)]
    assert kept_cells(cells, durations, 90 * MINUTE, 0 * MINUTE) == {GridCell(0, 0), GridCell(0, 1), GridCell(0, 2)}


def test_kept_cells_slack_absorbs_boundary_overage():
    cells = [GridCell(0, c, 51.0, -2.0 + c * 0.08) for c in range(4)]
    durations = [d * MINUTE for d in (90.0, 92.0, 93.0, 94.0)]
    slack = slack_minutes(4.0 * KM)  # ≈ 2.42
    assert kept_cells(cells, durations, 90 * MINUTE, slack) == {GridCell(0, 0), GridCell(0, 1)}
    assert GridCell(0, 2) not in kept_cells(cells, durations, 90 * MINUTE, slack)  # 93.0 > 90 + 2.42


def test_kept_cells_drops_unreachable():
    cells = [(0, 0, 51.0, -2.0), (0, 1, 51.0, -1.92)]
    cells = [GridCell(0, 0, 51.0, -2.0), GridCell(0, 1, 51.0, -1.92)]
    durations = [None, 10.0 * MINUTE]
    assert kept_cells(cells, durations, 90 * MINUTE, 0 * MINUTE) == {GridCell(0, 1)}


# ── raw → searches ───────────────────────────────────────────────────


def _raw_payload() -> dict:
    """One destination, Dad, on a 4×3 grid with synthetic durations.

    Cells within ~2 cells of the destination (centre of the grid) are kept,
    producing a small connected blob for union_outline to trace.
    """
    cells = [
        {"r": r, "c": c, "lat": round(51.0 + (r + 0.5) * 0.05, 5), "lon": round(-2.0 + (c + 0.5) * 0.08, 5),
         "duration_min": None if abs(r - 1.5) + abs(c - 1.0) > 2.5 else float(abs(r - 1.5) + abs(c - 1.0))}
        for r in range(4)
        for c in range(3)
    ]
    return {
        "metadata": {
            "engine_version": "drive-isochrone-v1",
            "profile": "driving-car",
            "speed_model": "free-flow",
            "threshold_min": 90,
            "cell_km": 4.0,
            "region_km": 153.0,
            "destinations": [{"label": "Dad", "postcode": "OX7 5GZ", "threshold_min": 90}],
            "generated_at": NOW,
            "count": 1,
        },
        "destinations": [
            {
                "label": "Dad",
                "postcode": "OX7 5GZ",
                "lat": 51.075,
                "lon": -1.92,
                "threshold_min": 90,
                "cell_km": 4.0,
                "slack_min": 2.42,
                "grid": {"lat_min": 51.0, "lat_max": 51.2, "lon_min": -2.0, "lon_max": -1.76},
                "cells": cells,
            }
        ],
    }


def test_raw_to_searches_schema_mirrors_searches_py():
    payload = raw_to_searches(_raw_payload(), generated_at=NOW)
    assert payload["metadata"]["count"] == len(payload["searches"]) == 1
    s = payload["searches"][0]
    assert set(s) >= {"id", "name", "polygon", "filters", "rightmove_url", "destination", "threshold_min"}
    assert s["id"] == "drive-dad-ox75gz-090"
    assert s["name"] == "Dad — 90 min drive"
    assert s["filters"] == {"min_beds": 2, "property_type": "houses"}
    assert s["threshold_min"] == 90
    assert s["destination"] == {"label": "Dad", "postcode": "OX7 5GZ", "lat": 51.075, "lon": -1.92}


def test_raw_to_searches_polygon_round_trips_via_url():
    s = raw_to_searches(_raw_payload(), generated_at=NOW)["searches"][0]
    poly = s["polygon"]
    assert len(poly) >= 4
    assert poly[0] != poly[-1]  # open loop; the URL builder closes it
    # vertices rounded to the polyline encode precision (URL round-trip contract)
    assert all((round(lat, 5), round(lon, 5)) == (lat, lon) for lat, lon in poly)
    assert parse_search_url(s["rightmove_url"]) == poly + [poly[0]]


def test_raw_to_searches_deterministic_modulo_timestamp():
    a = raw_to_searches(_raw_payload(), generated_at=NOW)
    b = raw_to_searches(_raw_payload(), generated_at="2026-08-03T10:00:00+00:00")
    assert a["searches"] == b["searches"]
    assert a["metadata"]["generated_at"] != b["metadata"]["generated_at"]


def test_raw_to_searches_ids_unique_across_destinations_and_loops():
    raw = _raw_payload()
    raw["destinations"].append(
        {
            "label": "Bracknell",
            "postcode": "RG12 8YA",
            "lat": 51.42,
            "lon": -0.75,
            "threshold_min": 60,
            "cell_km": 4.0,
            "slack_min": 2.42,
            "grid": raw["destinations"][0]["grid"],
            "cells": raw["destinations"][0]["cells"],
        }
    )
    payload = raw_to_searches(raw, generated_at=NOW)
    ids = [s["id"] for s in payload["searches"]]
    assert ids[0] == "drive-dad-ox75gz-090"
    assert ids[-1] == "drive-bracknell-rg128ya-060"
    assert len(ids) == len(set(ids))


def test_raw_to_searches_empty_destination_produces_no_search():
    raw = _raw_payload()
    raw["destinations"][0]["cells"] = [dict(c, duration_min=None) for c in raw["destinations"][0]["cells"]]
    assert raw_to_searches(raw, generated_at=NOW)["searches"] == []


def test_raw_to_searches_keeps_main_shed_below_island_threshold():
    """The island filter drops fringe speckles — never the destination's own
    shed: a 2-cell shed must still produce a search (a small-threshold or
    sparse-coverage config is valid)."""
    raw = _raw_payload()
    kept_two = [
        c for c in raw["destinations"][0]["cells"]
        if (c["r"], c["c"]) in {(1, 1), (1, 2)}
    ]
    raw["destinations"][0]["cells"] = kept_two
    searches = raw_to_searches(raw, generated_at=NOW, min_island_cells=4)["searches"]
    assert [s["id"] for s in searches] == ["drive-dad-ox75gz-090"]


# ── components / hole & island handling ─────────────────────────────


def test_components_are_four_connected():
    # edge-adjacent cells are one component; corner-touching cells are two
    assert _components({GridCell(0, 0), GridCell(1, 0), GridCell(1, 1)}) == [
        {GridCell(0, 0), GridCell(1, 0), GridCell(1, 1)}
    ]
    assert _components({GridCell(0, 0), GridCell(1, 1)}) == [{GridCell(0, 0)}, {GridCell(1, 1)}]


def test_signed_area_opposite_for_hole():
    outer = [GeoPoint(0.0, 0.0), GeoPoint(0.0, 1.0), GeoPoint(1.0, 1.0), GeoPoint(1.0, 0.0)]
    hole = [GeoPoint(0.0, 0.0), GeoPoint(1.0, 0.0), GeoPoint(1.0, 1.0), GeoPoint(0.0, 1.0)]  # reversed
    assert _signed_area(outer) == -_signed_area(hole)
    assert _signed_area(outer) != 0


def _hole_island_payload() -> dict:
    """6×6 grid: all kept except a moated 2×2 island and an interior hole.

    Island at rows 1-2/cols 1-2 (4 cells, moated by nulls); a true hole at
    (4,4) with kept neighbours on all sides. The main blob is everything else.
    """
    moat = {(0, 1), (0, 2), (1, 0), (1, 3), (2, 0), (2, 3), (3, 1), (3, 2)}
    hole = {(4, 4)}
    cells = []
    for r in range(6):
        for c in range(6):
            kept = (r, c) not in moat and (r, c) not in hole
            cells.append(
                {
                    "r": r,
                    "c": c,
                    "lat": round(51.0 + r * 0.05, 5),
                    "lon": round(-2.0 + c * 0.08, 5),
                    "duration_min": 0.0 if kept else None,
                }
            )
    return {
        "metadata": {
            "engine_version": "drive-isochrone-v1",
            "profile": "driving-car",
            "speed_model": "free-flow",
            "threshold_min": 90,
            "cell_km": 4.0,
            "region_km": 153.0,
            "destinations": [{"label": "Dad", "postcode": "OX7 5GZ", "threshold_min": 90}],
            "generated_at": NOW,
            "count": 1,
        },
        "destinations": [
            {
                "label": "Dad",
                "postcode": "OX7 5GZ",
                "lat": 51.2,
                "lon": -1.6,
                "threshold_min": 90,
                "cell_km": 4.0,
                "slack_min": 2.42,
                "grid": {"lat_min": 51.0, "lat_max": 51.3, "lon_min": -2.0, "lon_max": -1.52},
                "cells": cells,
            }
        ],
    }


def test_raw_to_searches_absorbs_hole_and_keeps_large_island():
    payload = raw_to_searches(_hole_island_payload(), generated_at=NOW)
    ids = [s["id"] for s in payload["searches"]]
    # main shed (no suffix) + 4-cell island (-2); the hole is NOT a search
    assert ids == ["drive-dad-ox75gz-090", "drive-dad-ox75gz-090-2"]
    island = payload["searches"][1]
    # island polygon covers ~its 2×2 cells (0.1° × 0.16° ≈ 0.016 deg²)
    area = abs(_signed_area([GeoPoint(lat, lon) for lat, lon in island["polygon"]]))
    assert 0.008 < area < 0.02


def test_raw_to_searches_drops_small_islands_below_threshold():
    payload = raw_to_searches(_hole_island_payload(), generated_at=NOW, min_island_cells=5)
    assert [s["id"] for s in payload["searches"]] == ["drive-dad-ox75gz-090"]


# ── point-in-polygon ─────────────────────────────────────────────────


def test_point_in_polygon():
    square = [GeoPoint(51.0, -2.0), GeoPoint(51.0, -1.9), GeoPoint(51.1, -1.9), GeoPoint(51.1, -2.0)]
    assert point_in_polygon(51.05, -1.95, square)
    assert not point_in_polygon(51.2, -1.95, square)
    # boundary counts as inside (conservative: never under-cover) — plain ray
    # casting returns False for on-edge/vertex points, so these pin the
    # on-segment check
    assert point_in_polygon(51.0, -1.95, square)  # south edge
    assert point_in_polygon(51.05, -1.9, square)  # east edge
    assert point_in_polygon(51.1, -1.95, square)  # north edge
    assert point_in_polygon(51.0, -1.9, square)  # a vertex


# ── validation ───────────────────────────────────────────────────────


def test_validate_payload_passes_valid_payload():
    payload = raw_to_searches(_raw_payload(), generated_at=NOW)
    assert validate_payload(payload) == []


def test_validate_payload_catches_too_many_vertices():
    payload = raw_to_searches(_raw_payload(), generated_at=NOW)
    poly = payload["searches"][0]["polygon"]
    payload["searches"][0]["polygon"] = poly + [(51.0 + i * 1e-4, -1.9 + i * 1e-4) for i in range(600)]
    issues = validate_payload(payload)
    assert any("vertices" in i for i in issues)


def test_validate_payload_catches_out_of_bbox_vertex():
    payload = raw_to_searches(_raw_payload(), generated_at=NOW)
    payload["searches"][0]["polygon"][0] = (57.0, -1.9)  # Sheffield's latitude — outside GB_BBOX
    assert any("bounding box" in i for i in validate_payload(payload))


def test_validate_payload_catches_url_polygon_mismatch():
    import tools.commute.rightmove_url as rmu

    payload = raw_to_searches(_raw_payload(), generated_at=NOW)
    poly = payload["searches"][0]["polygon"]
    shifted = [(lat + 0.1, lon) for lat, lon in poly]
    payload["searches"][0]["rightmove_url"] = rmu.build_search_url(shifted, min_beds=2, property_type="houses")
    assert any("round-trip" in i for i in validate_payload(payload))


def test_validate_payload_catches_count_mismatch():
    payload = raw_to_searches(_raw_payload(), generated_at=NOW)
    payload["metadata"]["count"] = 99
    assert any("count" in i for i in validate_payload(payload))


def test_validate_payload_catches_destination_lost():
    payload = raw_to_searches(_raw_payload(), generated_at=NOW)
    payload["searches"] = []
    assert any("Dad" in i for i in validate_payload(payload))


def test_validate_payload_flags_malformed_polygon_without_crashing():
    """A non-list polygon (hand-edited or partial artifact) must be flagged,
    not crash the validator with a TypeError."""
    payload = raw_to_searches(_raw_payload(), generated_at=NOW)
    payload["searches"][0]["polygon"] = "not a polygon"
    issues = validate_payload(payload)
    assert any("malformed" in i for i in issues)


def test_validate_payload_flags_missing_destination_without_crashing():
    """A search record without a destination dict must be flagged, not crash
    the destination-centre check with a KeyError."""
    payload = raw_to_searches(_raw_payload(), generated_at=NOW)
    payload["searches"][0]["destination"] = None
    issues = validate_payload(payload)
    assert any("malformed" in i for i in issues)


def test_validate_payload_flags_string_vertices_without_crashing():
    """Vertices with quoted numbers (e.g. hand-edited "51.0") must be
    flagged, not crash the GB-bbox check with a TypeError."""
    payload = raw_to_searches(_raw_payload(), generated_at=NOW)
    payload["searches"][0]["polygon"][0] = ["51.0", "-1.0"]
    issues = validate_payload(payload)
    assert any("malformed" in i for i in issues)


def test_validate_payload_flags_non_dict_destination_without_crashing():
    """A non-dict destination (e.g. "destination": "corrupt") must be
    flagged, not crash the label/grouping steps with an AttributeError."""
    payload = raw_to_searches(_raw_payload(), generated_at=NOW)
    payload["searches"][0]["destination"] = "corrupt"
    issues = validate_payload(payload)
    assert any("malformed" in i for i in issues)


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_combined_map_fails_cleanly_on_malformed_payload(tmp_path):
    """main() must exit with the two-tier message, not a KeyError traceback,
    when a payload is valid JSON but structurally wrong."""
    import json

    from tools.commute.combined_map import main as combined_main

    union_path = tmp_path / "union.json"
    union_path.write_text(json.dumps({"components": []}))
    drive_path = tmp_path / "drive.json"
    drive_path.write_text(json.dumps([1, 2, 3]))  # list-shaped: .get() raises AttributeError

    code = combined_main(
        [
            "--union", str(union_path),
            "--drive", str(drive_path),
            "--intersection", str(tmp_path / "none.json"),
            "--out", str(tmp_path / "map.html"),
        ]
    )
    assert code == 1
    assert not (tmp_path / "map.html").exists()


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_atomic_write_leaves_no_partial(tmp_path):
    """The artifact writer replaces via tmp + os.replace — a concurrent
    reader sees the old or the new file, never a truncated one, and no .tmp
    litter survives."""
    from tools.commute.drive_isochrone import _atomic_write

    out = tmp_path / "out" / "drive_searches.json"
    out.parent.mkdir()
    _atomic_write(out, "old")
    assert out.read_text() == "old"
    _atomic_write(out, "new")
    assert out.read_text() == "new"
    assert not list(out.parent.glob("*.tmp"))


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_combined_map_warns_when_drive_layers_absent(tmp_path, capsys):
    """A drive payload missing 'searches' must render the map with a
    warning — never silently produce a transit-only map that looks complete."""
    import json

    from tools.commute.combined_map import main as combined_main

    union_path = tmp_path / "union.json"
    union_path.write_text(json.dumps({"components": []}))
    drive_path = tmp_path / "drive.json"
    drive_path.write_text(json.dumps({"metadata": {"count": 0}}))  # no searches key

    code = combined_main(
        [
            "--union", str(union_path),
            "--drive", str(drive_path),
            "--intersection", str(tmp_path / "none.json"),
            "--out", str(tmp_path / "map.html"),
        ]
    )
    assert code == 0
    assert "no drive sheds" in capsys.readouterr().err


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_combined_map_degrades_on_malformed_intersection_records(tmp_path, capsys):
    """Records missing expected keys (hand-edited) must drop the layer, not
    crash the whole map build with a KeyError."""
    import json

    from tools.commute.combined_map import main as combined_main

    union_path = tmp_path / "union.json"
    union_path.write_text(json.dumps({"components": []}))
    drive_path = tmp_path / "drive.json"
    drive_path.write_text(json.dumps({"searches": []}))
    intersection_path = tmp_path / "intersection.json"
    intersection_path.write_text(json.dumps({"searches": [{"polygon": []}]}))  # missing url/name

    code = combined_main(
        [
            "--union", str(union_path),
            "--drive", str(drive_path),
            "--intersection", str(intersection_path),
            "--out", str(tmp_path / "map.html"),
        ]
    )
    assert code == 0
    assert (tmp_path / "map.html").exists()
    assert "malformed" in capsys.readouterr().err


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_combined_map_degrades_on_malformed_intersection(tmp_path, capsys):
    """A valid-JSON-but-wrong-shape intersection (non-empty list) must
    render the map WITHOUT the layer, not crash after promising to."""
    import json

    from tools.commute.combined_map import main as combined_main

    union_path = tmp_path / "union.json"
    union_path.write_text(json.dumps({"components": []}))
    drive_path = tmp_path / "drive.json"
    drive_path.write_text(json.dumps({"searches": []}))
    intersection_path = tmp_path / "intersection.json"
    intersection_path.write_text(json.dumps([1, 2, 3]))

    code = combined_main(
        [
            "--union", str(union_path),
            "--drive", str(drive_path),
            "--intersection", str(intersection_path),
            "--out", str(tmp_path / "map.html"),
        ]
    )
    assert code == 0
    assert (tmp_path / "map.html").exists()


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_combined_map_warns_when_intersection_layer_is_missing(tmp_path, capsys):
    """An EMPTY (supported) intersection must not silently vanish from the
    map — the pipeline surfaces the missing 'Where we could live' layer."""
    import json

    from tools.commute.combined_map import main as combined_main

    union_path = tmp_path / "union.json"
    union_path.write_text(json.dumps({"components": []}))
    drive_path = tmp_path / "drive.json"
    drive_path.write_text(json.dumps({"searches": []}))
    intersection_path = tmp_path / "intersection.json"
    intersection_path.write_text(json.dumps({"searches": []}))

    code = combined_main(
        [
            "--union", str(union_path),
            "--drive", str(drive_path),
            "--intersection", str(intersection_path),
            "--out", str(tmp_path / "map.html"),
        ]
    )
    assert code == 0
    assert "Where we could live" in capsys.readouterr().err
