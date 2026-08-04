from __future__ import annotations

from houses.geo import GeoPoint
from tests.unit.conftest import flush_all


def _inject_session(client) -> None:
    """Add a valid signed session cookie to the test client's default cookies."""
    from houses.web.auth import _make_session_cookie

    cookie = _make_session_cookie(
        email="simon@example.com",
        name="Simon",
        picture="",
        is_superuser=True,
    )
    client.cookies.set("session", cookie)


class TestPropertyApi:
    def _setup(self):
        from fastapi.testclient import TestClient

        from houses.property_registry import _registry
        from houses.server import app

        _registry.clear()
        client = TestClient(app)
        _inject_session(client)
        return client, _registry

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

        resp = client.get("/api/properties/all")
        assert resp.status_code == 200
        data = resp.json()
        assert "a" in data
        assert "b" in data

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

        client = TestClient(app)
        _inject_session(client)
        return client

    def test_patch_financial_with_dict(self):
        """PATCH /settings/financial must accept a dict body."""
        client = self._setup()
        resp = client.patch(
            "/api/settings/financial",
            json={"mortgage_rate": 0.04},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_put_persons_removed(self):
        """PUT /settings/persons (whole-list, no authz) must be gone."""
        client = self._setup()
        resp = client.put("/api/settings/persons", json=[{"name": "Simon", "has_car": True}])
        assert resp.status_code == 404, f"Expected 404 (endpoint removed), got {resp.status_code}"

    def test_reseed_endpoint_exists(self):
        """POST /api/admin/reseed must return a JSON response."""
        from unittest.mock import patch

        with patch("houses.sheets.reader.get_properties_data", return_value=[]):
            client = self._setup()
            resp = client.post("/api/admin/reseed")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def _push_persons(*persons) -> None:
    """Seed the persons settings node directly (module-level cache)."""
    from houses.services_provider import get_services

    get_services().persons_source.push(list(persons), "user")


class TestPatchPersonApi:
    """PATCH /api/settings/person/{name} — server-side ownership.

    The server must enforce who may edit whom; the UI hiding controls is
    never the enforcement (never trust the UI for ownership).
    """

    def _setup(self, *, superuser: bool = False):
        from fastapi.testclient import TestClient

        from houses.model.domain import Person, PlaceOfInterest
        from houses.server import app
        from houses.web.auth import _make_session_cookie

        _push_persons(
            Person(
                name="Simon",
                has_car=True,
                email="simon@example.com",
                places_of_interest=(PlaceOfInterest("Pimlico", "1 Drummond Gate, Pimlico, London SW1V 2QQ"),),
            ),
            Person(name="Lorena", has_car=False, email="lorena@example.com"),
            Person(
                name="George",
                has_car=False,
                is_child=True,
                editable_by=("Simon",),
                places_of_interest=(PlaceOfInterest("Primary School", ""),),
            ),
        )
        client = TestClient(app)
        client.cookies.set(
            "session",
            _make_session_cookie(email="simon@example.com", name="Simon", picture="", is_superuser=superuser),
        )
        return client

    def _person(self, client, name: str) -> dict:
        value = client.get("/api/settings").json()["persons"]["value"]
        return next(p for p in value if p["name"] == name)

    def test_requires_auth(self):
        """No session cookie → 401."""
        from fastapi.testclient import TestClient

        from houses.server import app

        _push_persons()
        client = TestClient(app)
        resp = client.patch("/api/settings/person/Simon", json={"name": "Simon", "has_car": True})
        assert resp.status_code == 401

    def test_own_person_allowed(self):
        client = self._setup()
        resp = client.patch("/api/settings/person/Simon", json={"name": "Simon", "has_car": False})
        assert resp.status_code == 200, resp.text[:300]

    def test_other_person_forbidden(self):
        """Session Simon editing Lorena's record → 403, never 200."""
        client = self._setup()
        resp = client.patch("/api/settings/person/Lorena", json={"name": "Lorena", "has_car": True})
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text[:300]}"

    def test_superuser_can_edit_anyone(self):
        client = self._setup(superuser=True)
        resp = client.patch("/api/settings/person/Lorena", json={"name": "Lorena", "has_car": True})
        assert resp.status_code == 200, resp.text[:300]

    def test_guardian_edits_child(self):
        """Simon is in George's editable_by → allowed even though Simon
        is not George."""
        client = self._setup()
        resp = client.patch(
            "/api/settings/person/George",
            json={"name": "George", "has_car": False, "is_child": True, "places_of_interest": []},
        )
        assert resp.status_code == 200, resp.text[:300]

    def test_unknown_person_404(self):
        client = self._setup()
        resp = client.patch("/api/settings/person/Nobody", json={"name": "Nobody", "has_car": True})
        assert resp.status_code == 404

    def test_updates_person_fields_and_thresholds(self):
        """PATCH persists POI modes/trips and (optionally) the person's
        commute thresholds in one call."""
        client = self._setup()
        resp = client.patch(
            "/api/settings/person/Simon",
            json={
                "name": "Simon",
                "has_car": True,
                "places_of_interest": [
                    {
                        "label": "Pimlico",
                        "address": "1 Drummond Gate, Pimlico, London SW1V 2QQ",
                        "trips_per_week": 3,
                        "weeks_per_year": 46,
                        "acceptable_modes": ["train", "car"],
                    }
                ],
                "thresholds": {"good_max_minutes": 25, "fine_max_minutes": 40},
            },
        )
        assert resp.status_code == 200, resp.text[:300]
        simon = self._person(client, "Simon")
        poi = simon["places_of_interest"][0]
        assert poi["acceptable_modes"] == ["train", "car"]
        assert poi["trips_per_week"] == 3
        thresholds = client.get("/api/settings").json()["commute_thresholds"]["value"]["Simon"]
        assert thresholds == {"good_max_minutes": 25, "fine_max_minutes": 40}

    def test_own_patch_cannot_escalate_to_superuser(self):
        """A non-superuser editing their own record must not be able to
        grant themselves is_superuser or hijack the email link."""
        client = self._setup()
        resp = client.patch(
            "/api/settings/person/Simon",
            json={"name": "Simon", "has_car": True, "is_superuser": True, "email": "hacker@example.com"},
        )
        assert resp.status_code == 200
        simon = self._person(client, "Simon")
        assert simon["is_superuser"] is False
        assert simon["email"] == "simon@example.com"

    def test_get_settings_carries_modes_editable_by_and_editable_by_me(self):
        """GET /settings must expose per-POI effective modes, per-person
        editable_by, and the session-aware editable_by_me flag."""
        client = self._setup()
        simon = self._person(client, "Simon")
        assert simon["editable_by"] == ["Simon"]
        assert simon["editable_by_me"] is True
        # unset modes migrate by rule: Pimlico → train
        assert simon["places_of_interest"][0]["acceptable_modes"] == ["train"]
        lorena = self._person(client, "Lorena")
        assert lorena["editable_by_me"] is False
        george = self._person(client, "George")
        assert george["editable_by_me"] is True  # Simon is George's guardian
        assert george["places_of_interest"][0]["acceptable_modes"] == ["walk"]  # school rule


class TestWorksEstimateApi:
    """PATCH /api/properties/{rid}/works-estimate endpoint."""

    def _setup(self):
        from fastapi.testclient import TestClient

        from houses.property_registry import _reset as _reset_registry
        from houses.server import app

        _reset_registry()
        client = TestClient(app)
        _inject_session(client)
        return client

    def test_patch_works_estimate_updates_value(self):
        """PATCH must update the works_estimates dict and return 200."""
        from houses.nodes.property import PropertyNodes
        from houses.property_registry import register_property

        rid = "12345678"

        client = self._setup()
        prop = PropertyNodes(rid)
        register_property(rid, prop)

        resp = client.patch(
            f"/api/properties/{rid}/works-estimate",
            json={"person": "Ashby", "value": 15000},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:500]}"
        assert resp.json() == {"status": "ok"}

    def test_works_estimate_propagates_to_detail(self):
        """After PATCH, GET detail must show updated mortgage
        WITHOUT requiring an explicit flush."""
        from money import Money

        from houses.geo import GeoPoint
        from houses.nodes.property import PropertyNodes
        from houses.property_registry import register_property

        rid = "22345678"

        client = self._setup()
        prop = PropertyNodes(rid)

        # Seed DAG data so mortgage_required computes
        prop.rightmove_price.push(Money("500000", "GBP"), "test")
        prop.rightmove_address.push("1 Test St", "test")
        prop.rightmove_bedrooms.push("3", "test")
        prop.rightmove_location.push(GeoPoint(51.5, -0.1), "test")
        prop.corrected_address.push("1 Test St, SW1V 2QQ", "test")
        prop.precise_location.push(GeoPoint(51.5, -0.1), "test")
        prop.postcode.push("SW1V 2QQ", "test")
        prop.user_entered_address.push("1 Test St, SW1V 2QQ", "test")
        prop.works_estimates.push({}, "test")
        from money import Money

        prop.rental_income.push(Money("0", "GBP"), "test")
        prop.comment_status.push("", "test")

        register_property(rid, prop)

        from tests.unit.conftest import flush_all

        # Two flushes needed: first processes TotalWorksNode/EquityTotalNode,
        # second processes MortgageRequiredNode → MonthlyMortgagePaymentNode
        # → TotalMonthlyHousingCostNode (two-level DAG wave).
        flush_all()
        flush_all()

        # Get baseline detail
        resp = client.get(f"/api/properties/{rid}/detail")
        assert resp.status_code == 200, resp.text[:500]
        detail_before = resp.json()

        af = detail_before.get("affordability")
        assert af is not None, "detail missing affordability"
        baseline_mortgage = af.get("mortgage_required")
        assert baseline_mortgage is not None

        # Push a works estimate via the PATCH endpoint
        resp = client.patch(
            f"/api/properties/{rid}/works-estimate",
            json={"person": "Ashby", "value": 20000},
        )
        assert resp.status_code == 200, resp.text[:500]

        # Re-fetch detail IMMEDIATELY — no manual flush.
        # The PATCH endpoint must flush the processor itself.
        resp = client.get(f"/api/properties/{rid}/detail")
        assert resp.status_code == 200, resp.text[:500]
        detail_after = resp.json()

        af_after = detail_after.get("affordability")
        updated_mortgage = af_after.get("mortgage_required")

        assert updated_mortgage is not None
        assert updated_mortgage != baseline_mortgage, (
            f"mortgage_required did not change after works update: "
            f"baseline={baseline_mortgage}, updated={updated_mortgage}"
        )
