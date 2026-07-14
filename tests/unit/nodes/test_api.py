from __future__ import annotations

from houses.geo import GeoPoint
from tests.unit.conftest import flush_all


class TestPropertyApi:
    def _setup(self):
        from fastapi.testclient import TestClient

        from houses.server import app
        from houses.web.api_router import _registry, api_router

        _registry.clear()
        app.include_router(api_router)
        return TestClient(app), _registry

    def test_get_property_returns_json(self):
        from houses.nodes.property import PropertyNodes

        client, reg = self._setup()
        prop = PropertyNodes("prop123")
        prop.precise_location.push(GeoPoint(51.5, -0.1), "user")
        prop.rightmove_location.push(GeoPoint(51.4, -0.2), "rightmove")
        prop.user_entered_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        prop.corrected_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        prop.rightmove_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        reg["prop123"] = prop
        flush_all()

        resp = client.get("/api/properties/prop123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rid"] == "prop123"
        assert data["best_location"]["status"] == "succeeded"
        assert data["best_location"]["value"] == {"lat": 51.5, "lon": -0.1}

    def test_get_property_404(self):
        client, _ = self._setup()
        resp = client.get("/api/properties/nonexistent")
        assert resp.status_code == 404

    def test_list_properties(self):
        from houses.nodes.property import PropertyNodes

        client, reg = self._setup()
        reg["a"] = PropertyNodes("a")
        reg["b"] = PropertyNodes("b")

        resp = client.get("/api/properties")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data["properties"]) == {"a", "b"}

    def test_all_route_not_caught_by_rid(self):
        """Route ordering: /properties/all must resolve before {rid}."""
        client, _ = self._setup()
        resp = client.get("/api/properties/all")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_detail_subroute_not_caught_by_rid(self):
        """Detail sub-route should not be caught by {rid}."""
        client, _ = self._setup()
        resp = client.get("/api/properties/nonexistent/detail")
        assert resp.status_code == 404

    def test_get_settings(self):
        client, _ = self._setup()
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


class TestSettingsApi:
    """Settings endpoints: /api/settings/* — must work with Body() annotation."""

    def _setup(self):
        from fastapi.testclient import TestClient

        from houses.server import app
        from houses.web.api_router import api_router

        app.include_router(api_router)
        return TestClient(app)

    def test_patch_persons_with_list(self):
        """PATCH /settings/persons must accept a raw JSON list body."""
        client = self._setup()
        resp = client.patch(
            "/api/settings/persons",
            json=[{"name": "Test", "has_car": False, "places_of_interest": []}],
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_patch_persons_empty_list(self):
        """PATCH /settings/persons with empty list must not crash."""
        client = self._setup()
        resp = client.patch("/api/settings/persons", json=[])
        assert resp.status_code == 200

    def test_patch_financial_with_dict(self):
        """PATCH /settings/financial must accept a dict body."""
        client = self._setup()
        resp = client.patch(
            "/api/settings/financial",
            json={"mortgage_rate": 0.04},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
