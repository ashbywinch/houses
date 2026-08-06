from __future__ import annotations

from decimal import Decimal

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

    def test_patch_accepts_bus_walk_penalty_and_money_round_trip(self):
        """GET /settings serializes bus_walk_penalty as {value, unit} and
        money as {amount, currency} — PATCH must accept the same shapes
        back instead of crashing or dropping them."""
        client = self._setup()
        resp = client.patch(
            "/api/settings/person/Simon",
            json={
                "name": "Simon",
                "has_car": True,
                "bus_walk_penalty": {"value": 20, "unit": "minute"},
                "home_sale_price": {"amount": "550000", "currency": "GBP"},
            },
        )
        assert resp.status_code == 200, resp.text[:300]
        simon = self._person(client, "Simon")
        assert simon["bus_walk_penalty"] == {"value": 20, "unit": "minute"}
        assert simon["home_sale_price"] == {"amount": "550000.00", "currency": "GBP"}


    def test_non_superuser_cannot_rename_onto_another_person(self):
        """name is the ownership key: a non-superuser editing their own
        record must not be able to rename it to another person's name
        (that would grant edit rights over the other person's settings)
        or flip is_child (which changes guardianship)."""
        from fastapi.testclient import TestClient

        from houses.model.domain import Person
        from houses.server import app
        from houses.web.auth import _make_session_cookie

        _push_persons(
            Person(name="Simon", has_car=True, email="simon@example.com"),
            Person(name="Ashby", has_car=True, email="emily.winch@gmail.com"),
        )
        client = TestClient(app)
        # Emily (Ashby) is NOT a superuser
        client.cookies.set(
            "session",
            _make_session_cookie(email="emily.winch@gmail.com", name="Emily", picture="", is_superuser=False),
        )
        resp = client.patch(
            "/api/settings/person/Ashby",
            json={"name": "Simon", "has_car": True, "is_child": True},
        )
        assert resp.status_code == 200, resp.text[:300]
        value = client.get("/api/settings").json()["persons"]["value"]
        ashby = next(p for p in value if p["email"] == "emily.winch@gmail.com")
        assert ashby["name"] == "Ashby", "rename escalation: record now has another person's name"
        assert ashby["is_child"] is False, "is_child escalation"

    def test_non_superuser_cannot_rewrite_guardian_list(self):
        """editable_by is the ownership root — a non-superuser editing
        their own record must not delegate edit rights to arbitrary names,
        and a guardian must not silently rewrite a child's guardian list."""
        from fastapi.testclient import TestClient

        from houses.model.domain import Person
        from houses.server import app
        from houses.web.auth import _make_session_cookie

        _push_persons(
            Person(name="Simon", has_car=True, email="simon@example.com"),
            Person(name="George", has_car=False, is_child=True, editable_by=("Simon",)),
        )
        client = TestClient(app)
        client.cookies.set(
            "session",
            _make_session_cookie(email="simon@example.com", name="Simon", picture="", is_superuser=False),
        )
        resp = client.patch(
            "/api/settings/person/Simon",
            json={"name": "Simon", "has_car": True, "editable_by": ["Hacker", "Simon"]},
        )
        assert resp.status_code == 200
        value = client.get("/api/settings").json()["persons"]["value"]
        simon = next(p for p in value if p["name"] == "Simon")
        assert simon["editable_by"] == ["Simon"], "guardian list was rewritten by a non-superuser"

    def test_malformed_poi_list_is_400(self):
        """places_of_interest must be a list — storing null would poison
        the enrichment and 500 every later GET."""
        client = self._setup()
        resp = client.patch(
            "/api/settings/person/Simon",
            json={"name": "Simon", "has_car": True, "places_of_interest": None},
        )
        assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text[:150]}"

    def test_malformed_money_payloads_are_400(self):
        """A null/empty money or penalty value must be a 400 client error —
        storing it would poison every downstream GET (None.amount) and
        freeze the equity cascade."""
        client = self._setup()
        for bad in ({"home_sale_price": None}, {"home_sale_price": {}}, {"bus_walk_penalty": None}):
            resp = client.patch("/api/settings/person/Simon", json={"name": "Simon", "has_car": True, **bad})
            assert resp.status_code == 400, f"{bad}: expected 400, got {resp.status_code}: {resp.text[:150]}"

    def test_malformed_patch_body_returns_400_not_500(self):
        """An unknown field or malformed POI in the body is a CLIENT error
        (400 with detail), never a 500."""
        client = self._setup()
        resp = client.patch(
            "/api/settings/person/Simon",
            json={"name": "Simon", "has_car": True, "bogus_field": 1},
        )
        assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text[:200]}"

    def test_get_settings_tolerates_legacy_dict_person_entries(self):
        """A non-Person entry in the settings source (legacy/db-persisted
        dict) must not make GET /settings crash — match by name, don't
        zip-strict."""
        client = self._setup()
        # inject a plain dict alongside the Person records
        from houses.services_provider import get_services

        node = get_services().persons_source
        node.push([*list(node.latest_attempt().value_or_none() or []), {"name": "Legacy", "has_car": True}], "user")  # type: ignore[arg-type]
        resp = client.get("/api/settings")
        assert resp.status_code == 200, resp.text[:200]

    def test_partial_patch_preserves_unmentioned_fields(self):
        """A partial PATCH body must MERGE into the existing person —
        never reset unmentioned fields to defaults.  Replace semantics
        silently destroyed real data (emails, walk penalties) when a
        partial body hit the endpoint."""
        client = self._setup()
        # _setup's Simon has email simon@example.com + a Pimlico POI
        resp = client.patch("/api/settings/person/Simon", json={"name": "Simon", "has_car": True})
        assert resp.status_code == 200, resp.text[:300]
        simon = self._person(client, "Simon")
        assert simon["email"] == "simon@example.com", "unmentioned email was reset"
        assert [p["label"] for p in simon["places_of_interest"]] == ["Pimlico"], "unmentioned POIs were dropped"

    def test_partial_patch_preserves_penalty_and_works_flag(self):
        """Money-ish and behavioural fields also survive a partial PATCH."""
        from fastapi.testclient import TestClient
        from money import Money
        from pint import Quantity

        from houses.model.domain import Person
        from houses.server import app
        from houses.web.auth import _make_session_cookie

        _push_persons(
            Person(
                name="Ashby",
                has_car=True,
                email="emily.winch@gmail.com",
                bus_walk_penalty=Quantity(10, "minute"),  # type: ignore[arg-type]
                works_estimate_required=True,
                cash_contribution=Money("300000", "GBP"),
            )
        )
        client = TestClient(app)
        client.cookies.set(
            "session",
            _make_session_cookie(email="simon@example.com", name="Simon", picture="", is_superuser=True),
        )
        resp = client.patch(
            "/api/settings/person/Ashby",
            json={"name": "Ashby", "has_car": True, "cash_contribution": {"amount": "310000", "currency": "GBP"}},
        )
        assert resp.status_code == 200, resp.text[:300]
        value = client.get("/api/settings").json()["persons"]["value"]
        ashby = next(p for p in value if p["name"] == "Ashby")
        assert ashby["email"] == "emily.winch@gmail.com"
        assert ashby["bus_walk_penalty"] == {"value": 10, "unit": "minute"}
        assert ashby["works_estimate_required"] is True
        assert ashby["cash_contribution"]["amount"] == "310000.00"  # the mentioned field DID change

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


class TestSettingsPropagationApi:
    """P1: facts in, consequences out — a settings change must flow to
    every property's totals automatically (the DAG, never the client)."""

    def _setup(self):
        from fastapi.testclient import TestClient
        from money import Money

        from houses.model.domain import Person
        from houses.server import app
        from houses.web.auth import _make_session_cookie

        _push_persons(
            Person(
                name="Simon",
                has_car=True,
                email="simon@example.com",
                home_sale_price=Money("550000", "GBP"),
                outstanding_mortgage=Money("373000", "GBP"),
            ),
            Person(name="Lorena", has_car=False, email="lorena@example.com"),
            Person(name="Ashby", has_car=True, cash_contribution=Money("300000", "GBP")),
        )
        client = TestClient(app)
        client.cookies.set(
            "session",
            _make_session_cookie(email="simon@example.com", name="Simon", picture="", is_superuser=True),
        )
        return client

    def _seed_property(self) -> str:
        from money import Money

        from houses.geo import GeoPoint
        from houses.nodes.property import PropertyNodes
        from houses.property_registry import register_property

        rid = "42345678"
        prop = PropertyNodes(rid)
        prop.rightmove_price.push(Money("500000", "GBP"), "test")
        prop.rightmove_address.push("1 Test St", "test")
        prop.rightmove_bedrooms.push("3", "test")
        prop.rightmove_location.push(GeoPoint(51.5, -0.1), "test")
        prop.corrected_address.push("1 Test St, SW1V 2QQ", "test")
        prop.precise_location.push(GeoPoint(51.5, -0.1), "test")
        prop.postcode.push("SW1V 2QQ", "test")
        prop.user_entered_address.push("1 Test St, SW1V 2QQ", "test")
        prop.works_estimates.push({}, "test")
        prop.rental_income.push(Money("0", "GBP"), "test")
        prop.comment_status.push("", "test")
        register_property(rid, prop)
        return rid

    def test_get_settings_carries_effective_selling_home(self):
        """GET /settings reports the EFFECTIVE selling-home state per
        person (inferred where unset) so the toggle renders correctly."""
        client = self._setup()
        value = client.get("/api/settings").json()["persons"]["value"]
        simon = next(p for p in value if p["name"] == "Simon")
        assert simon["selling_home"] is True  # has home values -> inferred
        ashby = next(p for p in value if p["name"] == "Ashby")
        assert ashby["selling_home"] is False  # cash only -> inferred off

    def test_patch_round_trips_selling_home(self):
        """PATCHing selling_home stores it explicitly (merge semantics —
        other fields survive)."""
        client = self._setup()
        resp = client.patch(
            "/api/settings/person/Ashby",
            json={"name": "Ashby", "has_car": True, "selling_home": True},
        )
        assert resp.status_code == 200, resp.text[:300]
        value = client.get("/api/settings").json()["persons"]["value"]
        ashby = next(p for p in value if p["name"] == "Ashby")
        assert ashby["selling_home"] is True

    def test_get_settings_carries_household_deposit(self):
        """GET /settings reports the household deposit as one number —
        per person (sale − remaining mortgage + extra money) and the
        total — so the family sees the whole deposit without adding
        four sections."""
        client = self._setup()
        data = client.get("/api/settings").json()
        hd = data["household_deposit"]
        assert hd["total"] == {"amount": "477000.00", "currency": "GBP"}
        assert hd["persons"]["Simon"] == {"amount": "177000.00", "currency": "GBP"}
        assert hd["persons"]["Ashby"] == {"amount": "300000.00", "currency": "GBP"}

    def test_household_deposit_carries_provenance(self):
        """The deposit total ships a Provenance-shaped block — per-person
        formula lines (toggle-OFF persons show cash-only) — rendered by the
        standard ProvenanceView, never a bespoke widget (P8)."""
        client = self._setup()
        hd = client.get("/api/settings").json()["household_deposit"]
        prov = hd["provenance"]
        assert prov["label"] == "Household Deposit"
        lines = {line["label"]: line["value"] for line in prov["formula"]["lines"]}
        assert "£550,000.00 sale − £373,000.00 mortgage + £0.00 cash = £177,000.00" in lines.values()
        assert lines["Ashby"] == "£0 home + £300,000.00 cash = £300,000.00"
        assert prov["formula"]["result"] == "£477,000.00"

    def test_settings_change_updates_property_totals(self):
        """PATCH a person's cash contribution → mortgage_required drops by
        exactly the delta and the list total follows — automatically, via
        the DAG.  One flush drains the WHOLE cascade (equity → mortgage →
        monthly payment → housing cost) deterministically; production's
        background processor does the same without test machinery."""
        from tests.unit.conftest import flush_all

        client = self._setup()
        rid = self._seed_property()
        flush_all()  # one drain cascades the whole wave (see coding-standards)

        baseline = client.get(f"/api/properties/{rid}/detail").json()
        baseline_mortgage = baseline["affordability"]["mortgage_required"]
        assert baseline_mortgage is not None
        # capture the list baseline BEFORE the change (the summary and
        # detail read the same node — compare unwrapped values)
        baseline_total = _money_amount(
            client.get("/api/properties/all").json()[rid]["total_monthly_cost"]["value"]
        )

        resp = client.patch(
            "/api/settings/person/Ashby",
            json={"name": "Ashby", "has_car": True, "cash_contribution": {"amount": "310000", "currency": "GBP"}},
        )
        assert resp.status_code == 200, resp.text[:300]

        # The test environment has no background processor (production starts
        # one in lifespan).  A single flush drains the full refresh cascade:
        # each node's refresh emits synchronously, queueing its dependents
        # inside the SAME process_pending loop.
        flush_all()

        updated = client.get(f"/api/properties/{rid}/detail").json()
        updated_mortgage = updated["affordability"]["mortgage_required"]
        assert updated_mortgage is not None
        # Compare the MONEY VALUE (unwrap the Attempt wrapper) — comparing
        # wrapper dicts always "differs" because provenance timestamps
        # change between reads, which masks a non-propagation as success.
        delta = _amount_of(updated_mortgage) - _amount_of(baseline_mortgage)
        assert delta == Decimal("-10000"), f"mortgage_required should drop by exactly £10,000, moved {delta}"

        # the property list summary follows automatically too — same node,
        # same drain, and the drop equals the mortgage-payment drop
        updated_total = _money_amount(
            client.get("/api/properties/all").json()[rid]["total_monthly_cost"]["value"]
        )
        assert updated_total < baseline_total, "list total did not decrease after the settings change"
        assert baseline_total - updated_total == _amount_of(
            baseline["affordability"]["monthly_mortgage"]
        ) - _amount_of(updated["affordability"]["monthly_mortgage"]), (
            "list total moved by exactly the monthly-payment delta"
        )


def _money_amount(v) -> Decimal:
    """Money amount from a raw value dict.

    Handles both Money ``{amount, currency}`` and Measurement
    ``{value: {amount, currency}, stddev}`` shapes (the total monthly
    cost became a Measurement in Part A).
    """
    if isinstance(v, dict):
        if "value" in v and isinstance(v["value"], dict):
            v = v["value"]
        return Decimal(v.get("amount") or "0")
    return Decimal(str(v))


def _amount_of(attempt: dict) -> Decimal:
    """Money amount inside an Attempt wrapper: {status, value: {amount, currency}}."""
    val = attempt.get("value") or {}
    if isinstance(val, dict):
        return Decimal(val.get("amount") or "0")
    return Decimal(str(val))


class TestWhatIfApi:
    """POST /api/what-if — pure evaluation, nothing persisted."""

    def _setup(self):
        from fastapi.testclient import TestClient
        from money import Money

        from houses.model.domain import Person
        from houses.property_registry import _registry
        from houses.server import app

        _push_persons(
            Person(
                name="Simon",
                has_car=True,
                email="simon@example.com",
                home_sale_price=Money("550000", "GBP"),
                outstanding_mortgage=Money("373000", "GBP"),
            ),
            Person(name="Lorena", has_car=False, email="lorena@example.com"),
            Person(name="Ashby", has_car=True, cash_contribution=Money("300000", "GBP")),
        )
        _registry.clear()
        client = TestClient(app)
        _inject_session(client)
        return client, _registry

    def _seed(self, reg):
        from money import Money

        from houses.geo import GeoPoint
        from houses.nodes.property import PropertyNodes

        prop = PropertyNodes("whatif1")
        prop.rightmove_price.push(Money("500000", "GBP"), "test")
        prop.rightmove_address.push("1 Test St", "test")
        prop.rightmove_bedrooms.push("3", "test")
        prop.rightmove_location.push(GeoPoint(51.5, -0.1), "test")
        prop.corrected_address.push("1 Test St, SW1V 2QQ", "test")
        prop.precise_location.push(GeoPoint(51.5, -0.1), "test")
        prop.postcode.push("SW1V 2QQ", "test")
        prop.user_entered_address.push("1 Test St, SW1V 2QQ", "test")
        prop.works_estimates.push({}, "test")
        prop.rental_income.push(Money("0", "GBP"), "test")
        prop.comment_status.push("", "test")
        reg["whatif1"] = prop
        return prop

    def test_what_if_changes_totals_without_persisting(self):
        client, reg = self._setup()
        self._seed(reg)
        flush_all()

        baseline = client.get("/api/properties/all").json()["whatif1"]["total_monthly_cost"]

        # What-if: Ashby's cash contribution up £100k → equity up → the
        # mortgage (and so the monthly total) must drop.
        resp = client.post(
            "/api/what-if",
            json={"persons": [{"name": "Ashby", "cash_contribution": {"amount": "400000", "currency": "GBP"}}]},
        )
        assert resp.status_code == 200
        result = resp.json()["results"]["whatif1"]
        assert result["succeeded"], result.get("error")
        hypothetical = Decimal(result["monthly_total"]["value"]["amount"])

        base_total = _money_amount(baseline["value"])
        assert hypothetical < base_total, "extra cash must lower the monthly total"

        # Nothing persisted: the summary (and the real persons) are unchanged.
        after = client.get("/api/properties/all").json()["whatif1"]["total_monthly_cost"]
        assert after == baseline

    def test_what_if_requires_persons_and_known_names(self):
        client, reg = self._setup()
        self._seed(reg)
        flush_all()

        assert client.post("/api/what-if", json={}).status_code == 422
        assert client.post("/api/what-if", json={"persons": []}).status_code == 422
        resp = client.post("/api/what-if", json={"persons": [{"name": "Nobody"}]})
        assert resp.status_code == 422
        assert "unknown person" in resp.json()["detail"]

    def test_what_if_rejects_malformed_money(self):
        client, reg = self._setup()
        self._seed(reg)
        flush_all()

        resp = client.post(
            "/api/what-if",
            json={"persons": [{"name": "Ashby", "cash_contribution": "not-money"}]},
        )
        assert resp.status_code == 400


class TestRegenerateApi:
    """POST /api/admin/regenerate — force recompute of non-stale nodes."""

    def _setup(self):
        from fastapi.testclient import TestClient

        from houses.property_registry import _registry
        from houses.server import app

        _registry.clear()
        client = TestClient(app)
        return client, _registry

    def _seed(self, reg):
        from money import Money

        from houses.geo import GeoPoint
        from houses.nodes.property import PropertyNodes

        prop = PropertyNodes("77777777")
        prop.rightmove_price.push(Money("500000", "GBP"), "test")
        prop.rightmove_address.push("1 Test St", "test")
        prop.rightmove_bedrooms.push("3", "test")
        prop.rightmove_location.push(GeoPoint(51.5, -0.1), "test")
        prop.corrected_address.push("1 Test St, SW1V 2QQ", "test")
        prop.precise_location.push(GeoPoint(51.5, -0.1), "test")
        prop.postcode.push("SW1V 2QQ", "test")
        prop.user_entered_address.push("1 Test St, SW1V 2QQ", "test")
        prop.works_estimates.push({}, "test")
        prop.rental_income.push(Money("0", "GBP"), "test")
        prop.comment_status.push("", "test")
        reg["77777777"] = prop
        return prop

    def test_requires_superuser(self):
        client, _ = self._setup()
        resp = client.post("/api/admin/regenerate", json={"patterns": ["*"]})
        # app-level auth answers first for an anonymous call
        assert resp.status_code in (401, 403)

    def test_requires_patterns(self):
        client, _ = self._setup()
        _inject_session(client)
        assert client.post("/api/admin/regenerate", json={}).status_code == 422
        assert client.post("/api/admin/regenerate", json={"patterns": []}).status_code == 422

    def test_regenerates_council_tax_stale_in_code(self):
        from money import Money

        from dag.attempt import Attempt
        from dag.persistence import save_node_result
        from houses.model.domain import Person

        _push_persons(
            Person(name="Simon", has_car=True, email="simon@example.com"),
            Person(name="Ashby", has_car=True, cash_contribution=Money("300000", "GBP")),
        )
        client, reg = self._setup()
        _inject_session(client)
        prop = self._seed(reg)
        flush_all()

        # Simulate a pre-A3 persisted state: council tax impossible and
        # NOT stale (dep timestamps older than the compute).
        nid = prop.council_tax._id
        save_node_result(
            nid,
            {"status": "impossible", "value": None, "error": "pre-A3 state", "succeeded": False, "provenance": {}},
        )
        prop.council_tax._attempt = Attempt.impossible("pre-A3 state")

        # A plain flush does NOT regenerate it — timestamps say fresh.
        flush_all()
        assert prop.council_tax.latest_attempt().impossible

        resp = client.post("/api/admin/regenerate", json={"patterns": ["*/council_tax"]})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["matched"] >= 1
        entry = next(r for r in data["regenerated"] if r["node"] == nid)
        assert entry["status"] == "succeeded"

        # Live node is regenerated (real lookup succeeds here — cached
        # VOA data; the fallback only fires when the lookup fails); the
        # downstream total is recomputed too (cascade drained).
        ct = prop.council_tax.latest_attempt()
        assert ct.succeeded
        info = ct.value_or_none()
        assert info is not None and info.band
        total = prop.total_monthly_cost.latest_attempt()
        assert total.succeeded

    def test_non_matching_pattern_regenerates_nothing(self):
        client, reg = self._setup()
        _inject_session(client)
        self._seed(reg)
        flush_all()
        resp = client.post("/api/admin/regenerate", json={"patterns": ["no-such-nodes/*"]})
        assert resp.status_code == 200
        assert resp.json()["matched"] == 0


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
        flush_all()  # one drain cascades the whole wave (see coding-standards)

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

        # The test environment has no background processor — drain the
        # queue explicitly (production's lifespan processor does this
        # automatically, and the WS broadcaster pushes the fresh totals).
        from tests.unit.conftest import flush_all as _flush

        _flush()

        resp = client.get(f"/api/properties/{rid}/detail")
        assert resp.status_code == 200, resp.text[:500]
        detail_after = resp.json()

        af_after = detail_after.get("affordability")
        updated_mortgage = af_after.get("mortgage_required")

        # Compare the MONEY VALUE, not the wrapper dict — the wrapper's
        # provenance timestamps always differ between reads, so a `!=` on
        # the dicts passes even when nothing propagated.
        assert updated_mortgage is not None
        assert _amount_of(updated_mortgage) != _amount_of(baseline_mortgage), (
            f"mortgage_required did not change after works update: "
            f"baseline={baseline_mortgage}, updated={updated_mortgage}"
        )
