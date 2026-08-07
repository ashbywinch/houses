"""Tests for houses/map_layers.py — the Map page's isochrone layers.

The layers come from the committed toolchain artifacts (union.json,
drive_searches.json, intersection.json) and must match the shape the
frontend's Leaflet map renders.
"""

from __future__ import annotations

import json


def _write(tmp_path, name: str, payload) -> None:
    (tmp_path / name).write_text(json.dumps(payload))


def test_layers_empty_without_artifacts(tmp_path, monkeypatch):
    import houses.map_layers as ml

    monkeypatch.setattr(ml, "UNION_PATH", tmp_path / "union.json")
    monkeypatch.setattr(ml, "DRIVE_PATH", tmp_path / "drive_searches.json")
    monkeypatch.setattr(ml, "INTERSECTION_PATH", tmp_path / "intersection.json")
    assert ml.isochrone_layers() == []


def test_layers_build_transit_drive_and_intersection(tmp_path, monkeypatch):
    import houses.map_layers as ml

    monkeypatch.setattr(ml, "UNION_PATH", tmp_path / "union.json")
    monkeypatch.setattr(ml, "DRIVE_PATH", tmp_path / "drive_searches.json")
    monkeypatch.setattr(ml, "INTERSECTION_PATH", tmp_path / "intersection.json")

    _write(
        tmp_path,
        "union.json",
        {"components": [{"outline": [[51.5, -0.1], [51.6, -0.1], [51.6, 0.0]]}, {"outline": [[52.0, 0.0]]}]},
    )
    _write(
        tmp_path,
        "drive_searches.json",
        {
            "searches": [
                {
                    "destination": {"label": "Dad"},
                    "polygon": [[51.9, -1.5], [52.0, -1.5]],
                    "name": "Dad — 90 min drive",
                    "rightmove_url": "https://rm/dad",
                }
            ]
        },
    )
    _write(
        tmp_path,
        "intersection.json",
        {
            "searches": [
                {"name": "All commutes", "polygon": [[51.9, -1.6]], "rightmove_url": "https://rm/all"}
            ]
        },
    )

    layers = ml.isochrone_layers()
    assert [layer["name"] for layer in layers] == ["Train: Pimlico & Aldgate", "Drive to Dad", "Where we could live"]

    train = layers[0]
    assert train["color"] == "#e33"
    assert len(train["polygons"]) == 2
    assert train["polygons"][0]["coords"][0] == [51.5, -0.1]

    drive = layers[1]
    assert drive["color"] == "#3a3"
    assert drive["polygons"][0]["url"] == "https://rm/dad"

    inter = layers[2]
    assert inter["color"] == "#c90"
    assert inter["fillOpacity"] == 0.25
    assert inter["polygons"][0]["name"] == "All commutes"


def test_endpoint_returns_layers(tmp_path, monkeypatch):
    """GET /api/map/isochrones returns the layers payload (auth required)."""
    from fastapi.testclient import TestClient

    import houses.map_layers as ml
    from houses.server import app
    from houses.web.auth import _make_session_cookie

    monkeypatch.setattr(ml, "UNION_PATH", tmp_path / "union.json")
    monkeypatch.setattr(ml, "DRIVE_PATH", tmp_path / "drive_searches.json")
    monkeypatch.setattr(ml, "INTERSECTION_PATH", tmp_path / "intersection.json")
    _write(tmp_path, "union.json", {"components": [{"outline": [[51.5, -0.1]]}]})

    client = TestClient(app)
    client.cookies.set(
        "session",
        _make_session_cookie(email="simon@example.com", name="Simon", picture="", is_superuser=False),
    )
    resp = client.get("/api/map/isochrones")
    assert resp.status_code == 200
    data = resp.json()
    assert "layers" in data
    assert data["layers"][0]["name"] == "Train: Pimlico & Aldgate"
