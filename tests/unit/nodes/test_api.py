from __future__ import annotations

from houses.geo import GeoPoint
from tests.unit.conftest import flush_all


class TestPropertyApi:
    def _setup(self):
        from fastapi.testclient import TestClient

        from houses.property_registry import _registry
        from houses.server import app

        _registry.clear()
        # Routes already registered by server.py — no app.include_router needed
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

        return TestClient(app)

    def test_put_persons_with_list(self):
        """PUT /settings/persons must accept a raw JSON list body."""
        client = self._setup()
        resp = client.put(
            "/api/settings/persons",
            json=[{"name": "Simon", "has_car": True}],
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"

    def test_put_persons_rejects_empty_list(self):
        """PUT /settings/persons with empty list must return 400."""
        client = self._setup()
        resp = client.put("/api/settings/persons", json=[])
        assert resp.status_code == 400, f"Expected 400 for empty list, got {resp.status_code}"

    def test_patch_financial_with_dict(self):
        """PATCH /settings/financial must accept a dict body."""
        client = self._setup()
        resp = client.patch(
            "/api/settings/financial",
            json={"mortgage_rate": 0.04},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_reseed_endpoint_exists(self):
        """POST /api/admin/reseed must return a JSON response."""
        from unittest.mock import patch

        with patch("houses.sheets.reader.get_properties_data", return_value=[]):
            client = self._setup()
            resp = client.post("/api/admin/reseed")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
