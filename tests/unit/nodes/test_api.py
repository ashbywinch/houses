from __future__ import annotations

from dag.attempt import Provenance
from houses.geo import GeoPoint


class TestPropertyApi:
    def test_get_property_returns_json(self):
        from fastapi.testclient import TestClient

        from houses.nodes.property import PropertyNodes
        from houses.server import app
        from houses.web.api_router import _registry, api_router

        prop = PropertyNodes("prop123")
        prop.precise_location.push(GeoPoint(51.5, -0.1), Provenance("user"))
        _registry["prop123"] = prop

        app.include_router(api_router)
        client = TestClient(app)
        resp = client.get("/api/properties/prop123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rid"] == "prop123"
        assert data["best_location"]["succeeded"] is True
        assert data["best_location"]["value"] == {"lat": 51.5, "lon": -0.1}

    def test_get_property_404(self):
        from fastapi.testclient import TestClient

        from houses.server import app
        from houses.web.api_router import api_router

        app.include_router(api_router)
        client = TestClient(app)
        resp = client.get("/api/properties/nonexistent")
        assert resp.status_code == 404

    def test_list_properties(self):
        from fastapi.testclient import TestClient

        from houses.nodes.property import PropertyNodes
        from houses.server import app
        from houses.web.api_router import _registry, api_router

        _registry.clear()
        _registry["a"] = PropertyNodes("a")
        _registry["b"] = PropertyNodes("b")

        app.include_router(api_router)
        client = TestClient(app)
        resp = client.get("/api/properties")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data["properties"]) == {"a", "b"}

    def test_get_settings(self):
        from fastapi.testclient import TestClient

        from houses.server import app
        from houses.web.api_router import api_router

        app.include_router(api_router)
        client = TestClient(app)
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "bus_walk_penalty_minutes" in data
