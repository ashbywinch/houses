"""Tests for houses/map_layers.py — the Map page's isochrone layers.

The layers come from the committed toolchain artifacts (union.json,
drive_searches.json, intersection.json) and must match the shape the
frontend's Leaflet map renders.
"""

from __future__ import annotations

import json

from houses.web.api_router import IsochronePaths


def _write(tmp_path, name: str, payload) -> None:
    (tmp_path / name).write_text(json.dumps(payload))


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_layers_empty_without_artifacts(tmp_path):
    import houses.map_layers as ml

    assert (
        ml.isochrone_layers(
            union_path=tmp_path / "union.json",
            drive_path=tmp_path / "drive_searches.json",
            intersection_path=tmp_path / "intersection.json",
        )
        == []
    )


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_layers_build_transit_drive_and_intersection(tmp_path):
    import houses.map_layers as ml

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

    layers = ml.isochrone_layers(
        union_path=tmp_path / "union.json",
        drive_path=tmp_path / "drive_searches.json",
        intersection_path=tmp_path / "intersection.json",
    )
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
    # Only the intersection ("where we could live") shows by default —
    # the three isochrones start hidden behind the key.
    assert inter["visibleByDefault"] is True
    assert "visibleByDefault" not in layers[0]
    assert "visibleByDefault" not in layers[1]


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_endpoint_returns_layers(tmp_path):
    """GET /api/map/isochrones returns the layers payload (auth required)."""
    from fastapi.testclient import TestClient

    from houses.server import app
    from houses.web import api_router
    from houses.web.auth import _make_session_cookie
    app.dependency_overrides[api_router._isochrone_paths] = lambda: IsochronePaths(
        union=tmp_path / "union.json",
        drive=tmp_path / "drive_searches.json",
        intersection=tmp_path / "intersection.json",
    )
    try:
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
    finally:
        app.dependency_overrides.pop(api_router._isochrone_paths, None)
