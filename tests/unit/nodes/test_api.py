from __future__ import annotations

import typing
from decimal import Decimal

import pytest

from houses.geopoint import GeoPoint
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

        from houses.server import app
        from houses.services_provider import get_services

        registry = get_services().property_registry
        registry.clear()
        client = TestClient(app)
        _inject_session(client)
        return client, registry

    def test_get_property_returns_json(self):
        from houses.nodes.property_nodes import PropertyNodes

        client, reg = self._setup()
        prop = PropertyNodes("prop123")
        prop.precise_location.push(GeoPoint(51.5, -0.1), "user")
        prop.rightmove_location.push(GeoPoint(51.4, -0.2), "rightmove")
        prop.user_entered_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        prop.corrected_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        prop.rightmove_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        reg.register("prop123", prop)
        flush_all()

        resp = client.get("/api/properties/prop123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rid"] == "prop123"
        assert data["best_location"]["status"] == "succeeded"
        assert data["best_location"]["value"] == {"lat": 51.5, "lon": -0.1}

    def test_patch_address_recomputes_best_address(self):
        """PATCH /properties/{rid}/address must surface the corrected
        address in the immediate detail refetch (the C2 edit flow)."""
        from houses.nodes.property_nodes import PropertyNodes

        client, reg = self._setup()
        prop = PropertyNodes("prop123")
        prop.rightmove_address.push("10 High St", "Rightmove")
        reg.register("prop123", prop)
        flush_all()

        detail_before = client.get("/api/properties/prop123/detail").json()
        assert detail_before["best_address"]["value"] == "10 High St"

        resp = client.patch("/api/properties/prop123/address", json={"address": "20 New Rd, London"})
        assert resp.status_code == 200

        detail_after = client.get("/api/properties/prop123/detail").json()
        assert detail_after["best_address"]["value"] == "20 New Rd, London"

    def test_patch_address_drains_cascade_before_responding(self):
        """The PATCH must recompute the downstream DAG (council tax, EPC)
        BEFORE responding — the frontend refetches immediately and would
        otherwise race the background cascade and show stale figures."""
        from houses.nodes.property_nodes import PropertyNodes
        from houses.services_provider import get_services

        client, reg = self._setup()
        prop = PropertyNodes("prop123")
        prop.rightmove_address.push("10 High St", "Rightmove")
        prop.postcode.push("SW1V 2QQ", "Rightmove")
        reg.register("prop123", prop)
        flush_all()

        from tests.helpers import FakeEPC

        epc_svc = typing.cast(FakeEPC, get_services().epc_service)
        epc_svc.calls.clear()

        resp = client.patch(
            "/api/properties/prop123/address",
            json={"address": "20 New Rd, London SW1V 2QQ"},
        )
        assert resp.status_code == 200

        assert any(addr == "20 New Rd, London SW1V 2QQ" for _, addr in epc_svc.calls), (
            f"EPC must be recomputed with the new address before the PATCH returns, calls={epc_svc.calls}"
        )
        detail = client.get("/api/properties/prop123/detail").json()
        assert detail["affordability"]["council_tax"]["succeeded"]

    def test_patch_council_tax_rejects_non_bool_ignored(self):
        """`"ignored": "false"` (a string) or 1 must be a 422 — bool("false")
        is True and would silently hide the annexe."""
        from houses.nodes.property_nodes import PropertyNodes

        client, reg = self._setup()
        prop = PropertyNodes("prop124")
        reg.register("prop124", prop)
        flush_all()

        for bad in ("false", 1, "true", None):
            resp = client.patch("/api/properties/prop124/council-tax", json={"ignored": bad})
            assert resp.status_code == 422, (
                f"ignored={bad!r}: expected 422, got {resp.status_code}: {resp.text[:150]}"
            )

    def test_patch_council_tax_validates_all_fields_before_any_push(self):
        """A body with valid payers but an invalid ignored must 422
        WITHOUT persisting the payer choices (no partial updates)."""
        from houses.nodes.property_nodes import PropertyNodes

        client, reg = self._setup()
        prop = PropertyNodes("prop127")
        reg.register("prop127", prop)
        flush_all()

        resp = client.patch(
            "/api/properties/prop127/council-tax",
            json={"main_payers": ["Simon"], "ignored": "false"},
        )
        assert resp.status_code == 422
        # The payer choice must NOT have been persisted.
        assert prop.council_tax_payers.latest_attempt().value_or_none() in (None, [])

    def test_council_tax_payer_choice_survives_property_reload(self):
        """The default-push guard must only fire when the node was NEVER
        set — a saved payer choice persists across PropertyNodes
        reconstruction (the persisted row loads eagerly in __init__)."""
        from houses.nodes.property_nodes import PropertyNodes

        client, reg = self._setup()
        prop = PropertyNodes("prop125")
        reg.register("prop125", prop)
        flush_all()

        resp = client.patch(
            "/api/properties/prop125/council-tax",
            json={"main_payers": ["Simon"], "annexe_payers": ["Ashby"], "ignored": True},
        )
        assert resp.status_code == 200

        # Reconstruct the property from the persisted rows — the choice
        # must NOT be clobbered by the constructor's default push.
        prop2 = PropertyNodes("prop125")
        assert prop2.council_tax_payers.latest_attempt().value_or_none() == ["Simon"]
        assert prop2.annexe_payers.latest_attempt().value_or_none() == ["Ashby"]
        assert prop2.annexe_ignored.latest_attempt().value_or_none() is True

    @pytest.mark.asyncio
    async def test_refresh_code_stale_nodes_walks_the_commute_pipeline(self):
        """The lazy code-version refresh must reach nodes stored inside
        containers — the commute selectors and their sub-pipeline are in
        dicts/attrs, not direct attributes of the property."""
        from dag.scheduler import flush_processor
        from houses.model.domain import HomeCoOwner, Person, PlaceOfInterest
        from houses.nodes.property_nodes import PropertyNodes
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        svc = make_services()
        token = _sp.set(svc)
        try:
            _push_persons(
                Person(
                    name="Simon",
                    has_car=True,
                    places_of_interest=(PlaceOfInterest("Office", "SW1V 2QQ"),),
                    home_co_owners=(HomeCoOwner(name="Lorena", share=50),),
                ),
                Person(name="Lorena", has_car=False),
            )
            client, reg = self._setup()
            prop = PropertyNodes("prop126")
            prop.rightmove_address.push("1 Test St", "test")
            reg.register("prop126", prop)
            await flush_processor()

            assert prop.commute_selectors, "a person with a POI must build commute selectors"
            selector = next(iter(prop.commute_selectors.values()))
            # Simulate a deploy: the selector's persisted result came from
            # old code. vars(prop) alone would never find it.
            selector._persisted_code_version = "stale-code"
            assert selector.code_is_stale() is True

            await prop.refresh_code_stale_nodes()

            assert selector.code_is_stale() is False, (
                "the commute selector must be recomputed by the stale scan"
            )
        finally:
            _sp.reset(token)

    def test_patch_council_tax_sets_payers_and_detail_exposes_them(self):
        """PATCH /properties/{rid}/council-tax persists the apportionment
        and the detail payload exposes the bills + the choices."""
        from money import Money

        from dag.attempt import Attempt
        from dag.measurement import Measurement
        from houses.council_tax_info import AnnexeDwelling, CouncilTaxInfo
        from houses.nodes.property_nodes import PropertyNodes
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        class _CTWithAnnexe:
            async def lookup(self, postcode, address=""):
                return Attempt.succeeded(
                    CouncilTaxInfo(
                        band="D",
                        yearly_cost=Measurement(Money("1800", "GBP"), 0.0),
                        annexe=AnnexeDwelling(
                            address="FLAT 2, 10 HIGH ST",
                            band="A",
                            yearly_cost=Measurement(Money("900", "GBP"), 0.0),
                        ),
                    )
                )

        svc = make_services(council_tax_service=_CTWithAnnexe())
        token = _sp.set(svc)
        try:
            client, reg = self._setup()
            prop = PropertyNodes("prop123")
            prop.rightmove_address.push("10 High St", "Rightmove")
            prop.postcode.push("SW1V 2QQ", "Rightmove")
            reg.register("prop123", prop)
            flush_all()

            detail = client.get("/api/properties/prop123/detail").json()
            assert detail["affordability"]["council_tax"]["value"]["annexe"]["band"] == "A"

            resp = client.patch(
                "/api/properties/prop123/council-tax",
                json={"main_payers": ["Simon", "Lorena"], "annexe_payers": ["Ashby"], "ignored": False},
            )
            assert resp.status_code == 200

            detail = client.get("/api/properties/prop123/detail").json()
            apportionment = detail["council_tax_apportionment"]
            assert apportionment["main_payers"]["value"] == ["Simon", "Lorena"]
            assert apportionment["annexe_payers"]["value"] == ["Ashby"]
            assert apportionment["ignored"]["value"] is False
        finally:
            _sp.reset(token)

    def test_annexe_apportionment_changes_user_visible_total(self):
        """PATCHing the annexe payers must change the monthly cost the
        detail page renders — the settings drive the DAG, end to end, not
        just the stored inputs."""
        from money import Money
        from pint import Quantity

        from dag.attempt import Attempt
        from dag.measurement import Measurement
        from houses.council_tax_info import AnnexeDwelling, CouncilTaxInfo
        from houses.model.domain import HomeCoOwner, Person
        from houses.nodes.property_nodes import PropertyNodes
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        class _AnnexeCT:
            async def lookup(self, postcode, address=""):
                return Attempt.succeeded(
                    CouncilTaxInfo(
                        band="D",
                        yearly_cost=Measurement(Money("1800", "GBP"), 0.0),
                        annexe=AnnexeDwelling(
                            address="FLAT 2, 2 WILLOWMEAD GARDENS",
                            band="A",
                            yearly_cost=Measurement(Money("900", "GBP"), 0.0),
                        ),
                    )
                )

        svc = make_services(council_tax_service=_AnnexeCT())
        token = _sp.set(svc)
        try:
            # Clean family: no POIs (no TfL chain in unit tests), no
            # works-estimate requirement — so the money cascade resolves.
            _push_persons(
                Person(
                    name="Simon",
                    has_car=True,
                    bus_walk_penalty=Quantity(20, "minute"),
                    home_co_owners=(HomeCoOwner(name="Lorena", share=50),),
                ),
                Person(name="Lorena", has_car=False, bus_walk_penalty=Quantity(15, "minute")),
                Person(name="Ashby", has_car=True, bus_walk_penalty=Quantity(10, "minute")),
            )
            client, reg = self._setup()
            rid = "42345679"
            prop = PropertyNodes(rid)
            prop.rightmove_price.push(Money("500000", "GBP"), "test")
            prop.rightmove_address.push("1 Test St", "test")
            prop.postcode.push("SW1V 2QQ", "test")
            prop.works_estimates.push({}, "test")
            prop.rental_income.push(Money("0", "GBP"), "test")
            prop.comment_status.push("", "test")
            reg.register(rid, prop)

            flush_all()

            detail = client.get(f"/api/properties/{rid}/detail").json()
            group = detail["affordability"]["group_monthly_cost"]["value"]
            others_before = float(group["others"]["value"])
            couple_before = float(group["couple"]["value"])
            assert "annexe_council_tax" not in (group.get("others_breakdown") or {})

            # Main bill: Simon+Lorena pay it ALL → the couple takes the
            # couple's default share plus Ashby's ⅓ (£50/mo); the others'
            # total drops by exactly that main share.
            resp = client.patch(
                f"/api/properties/{rid}/council-tax",
                json={"main_payers": ["Simon", "Lorena"]},
            )
            assert resp.status_code == 200
            detail = client.get(f"/api/properties/{rid}/detail").json()
            group = detail["affordability"]["group_monthly_cost"]["value"]
            assert float(group["couple_breakdown"]["council_tax"]) == pytest.approx(150, abs=0.01), (
                "the couple must pay the WHOLE main bill when they are the only payers"
            )
            assert float(group["others_breakdown"]["council_tax"]) == pytest.approx(0, abs=0.01), (
                "others must stop paying the main bill when only the owners pay it"
            )
            assert float(group["others"]["value"]) == pytest.approx(others_before - 50, abs=0.02)
            assert float(group["couple"]["value"]) == pytest.approx(couple_before + 50, abs=0.02)

            # Annex bill: Ashby alone pays it → +£75/mo on the others.
            resp = client.patch(
                f"/api/properties/{rid}/council-tax",
                json={"annexe_payers": ["Ashby"], "ignored": False},
            )
            assert resp.status_code == 200
            detail = client.get(f"/api/properties/{rid}/detail").json()
            group = detail["affordability"]["group_monthly_cost"]["value"]
            others_with_annexe = float(group["others"]["value"])
            assert others_with_annexe == pytest.approx(others_before - 50 + 75, abs=0.01), (
                f"annexe share must land in the visible total, got {others_with_annexe}"
            )
            assert float(group["others_breakdown"]["annexe_council_tax"]) == pytest.approx(75, abs=0.01)

            # "Not related" → the annexe drops back out; main payers keep.
            client.patch(f"/api/properties/{rid}/council-tax", json={"ignored": True})
            detail = client.get(f"/api/properties/{rid}/detail").json()
            group = detail["affordability"]["group_monthly_cost"]["value"]
            assert float(group["others"]["value"]) == pytest.approx(others_before - 50, abs=0.01)
        finally:
            _sp.reset(token)

    def test_get_property_404(self):
        client, _ = self._setup()
        resp = client.get("/api/properties/nonexistent")
        assert resp.status_code == 404

    def test_list_properties(self):
        from houses.nodes.property_nodes import PropertyNodes

        client, reg = self._setup()
        reg.register("a", PropertyNodes("a"))
        reg.register("b", PropertyNodes("b"))

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

    def test_current_homes_and_rid_routes_both_resolve(self):
        """Regression: the 'choose a current house' dropdown was always
        empty on the settings page — /api/properties/current-homes was
        captured by the /api/properties/{rid} route (declared earlier),

        returning 'Property current-homes not found'.

        Contract: the two routes EACH resolve to their own handler — a
        shadowing bug in either direction breaks one of them, so the
        routing contract is tested from both callers' perspectives: the
        literal route returns the homes list, and a real property RID
        still returns that property."""
        from money import Money

        from houses.nodes.property_nodes import PropertyNodes
        from houses.property_registry import register_property

        client, _ = self._setup()
        prop = PropertyNodes("88275093")
        prop.rightmove_price.push(Money("500000", "GBP"), "test")
        prop.rightmove_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        prop.rightmove_bedrooms.push("3", "test")
        prop.rightmove_location.push(GeoPoint(51.5, -0.1), "test")
        prop.corrected_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        prop.precise_location.push(GeoPoint(51.5, -0.1), "test")
        prop.postcode.push("UB2 4GN", "test")
        prop.user_entered_address.push("31 Isambard Road, Southall, UB2 4GN", "test")
        prop.works_estimates.push({}, "test")
        prop.rental_income.push(Money("0", "GBP"), "test")
        prop.comment_status.push("current", "test")
        register_property("88275093", prop)
        # A non-current property must not appear in the homes list
        other = PropertyNodes("99999999")
        other.comment_status.push("", "test")
        register_property("99999999", other)
        flush_all()

        # The literal route resolves to the homes list (not a {rid} lookup)
        resp = client.get("/api/properties/current-homes")
        assert resp.status_code == 200, resp.text
        assert resp.json()["homes"] == [{"rid": "88275093", "address": "31 Isambard Road, Southall, UB2 4GN"}]

        # AND the parameterised route still resolves to its own property
        # (a reorder that shadows {rid} would fail here)
        resp = client.get("/api/properties/88275093")
        assert resp.status_code == 200, resp.text
        assert resp.json()["rid"] == "88275093"

        # A non-current RID is still a valid property lookup — only the
        # homes FILTER excluded it, not the routing
        resp = client.get("/api/properties/99999999")
        assert resp.status_code == 200, resp.text
        assert resp.json()["rid"] == "99999999"

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


def _provenance_walk(node, path, out) -> None:
    """Collect every (path, value) leaf of a provenance dict."""
    out.append((path, node.get("value")))
    for key, child in (node.get("sources") or {}).items():
        _provenance_walk(child, f"{path}/{key.split('/')[-1]}", out)


def _seed_property() -> str:
    """Seed a fully-populated property (default persons + commutes +
    council tax + works) and return its rid. Module-level so the
    fail-fast guard tests and the settings-propagation tests share it
    without cross-class self reuse."""
    from money import Money

    from houses.geopoint import GeoPoint
    from houses.nodes.property_nodes import PropertyNodes
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


def _iter_provenance(value, path, out) -> None:
    """Collect every (path, provenance-dict) in a JSON response — any
    object carrying a ``provenance`` key, at any depth. The guard walks
    the WHOLE detail response (epc, commutes, area, location, ...), not
    just the affordability block."""
    if isinstance(value, dict):
        if isinstance(value.get("provenance"), dict):
            out.append((path, value["provenance"]))
        for key, child in value.items():
            if key == "provenance":
                continue
            _iter_provenance(child, f"{path}/{key}", out)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            _iter_provenance(child, f"{path}[{i}]", out)


class TestProvenanceUserFriendly:
    """The FAIL-FAST guard: provenance values must be human, never raw
    machine dumps. A future node whose projection returns a dict that
    isn't one of the allowlisted friendly shapes fails here instead of
    shipping machine text to the provenance tree."""

    def _setup(self):
        from fastapi.testclient import TestClient

        from houses.server import app
        from houses.services_provider import get_services

        registry = get_services().property_registry
        registry.clear()
        client = TestClient(app)
        _inject_session(client)
        return client, registry

    def _seed(self):
        client, reg = self._setup()
        rid = _seed_property()
        from tests.unit.conftest import flush_all

        flush_all()
        return client, rid

    @staticmethod
    def _friendly(v) -> bool:
        if v is None or isinstance(v, (str, int, float, bool)):
            return True
        if isinstance(v, dict):
            # allowlisted: per-person money maps {"Ashby": "GBP 25,000.00"}
            # render as "Ashby: £25,000.00"; an empty dict renders nothing.
            if not v:
                return True
            return all(isinstance(k, str) and isinstance(val, str) and val.startswith("GBP ") for k, val in v.items())
        if isinstance(v, list):
            # allowlisted: named-object lists (persons) — the UI renders names.
            return all(isinstance(i, dict) and "name" in i for i in v)
        return False

    def test_all_provenance_values_are_user_friendly(self):
        """Every provenance value in a full property detail must be scalar
        or an allowlisted friendly shape — never a raw machine dict."""
        client, rid = self._seed()
        detail = client.get(f"/api/properties/{rid}/detail").json()

        bad: list[tuple[str, object]] = []
        provenances: list[tuple[str, dict]] = []
        _iter_provenance(detail, "detail", provenances)
        for section, prov in provenances:
            leaves: list[tuple[str, object]] = []
            _provenance_walk(prov, section, leaves)
            for path, value in leaves:
                if not self._friendly(value):
                    bad.append((path, value))
        assert provenances, "no provenance found — the guard is not exercising the response"
        assert not bad, "non-user-friendly provenance values:\n" + "\n".join(
            f"{path}: {value!r}" for path, value in bad
        )

    def test_commute_provenance_values_all_carry_destination_and_frequency(self):
        """Every commute in the provenance must use the ONE canonical
        structure — mode · duration · cost to <destination> · Nx/wk ·
        M wks/yr. A commute without a destination or frequency fails here."""
        client, rid = self._seed()
        detail = client.get(f"/api/properties/{rid}/detail").json()

        mode_prefix = ("Transit ", "Driving ", "Walking ", "Drive ", "Car ")
        bad: list[tuple[str, str]] = []
        seen = 0
        provenances: list[tuple[str, dict]] = []
        _iter_provenance(detail, "detail", provenances)
        for section, prov in provenances:
            leaves: list[tuple[str, object]] = []
            _provenance_walk(prov, section, leaves)
            for path, value in leaves:
                if not isinstance(value, str) or not value.startswith(mode_prefix):
                    continue
                seen += 1
                if " to " not in value or "x/wk" not in value or "wks/yr" not in value:
                    bad.append((path, value))
        assert seen > 0, "no commute values found — the guard is not exercising the tree"
        assert not bad, "commute provenance values missing destination/frequency:\n" + "\n".join(
            f"{path}: {value!r}" for path, value in bad
        )


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

    def test_get_settings_financial_reflects_patch(self):
        """GET /settings financial must return the LIVE node values —
        a patched rate shows up on the next read (regression: the old
        aggregate blob went stale after PATCH)."""
        from houses.nodes.settings_node import SETTING_DEFAULTS, aggregate_dict
        from houses.services_provider import get_services

        client = self._setup()
        before = client.get("/api/settings").json()["financial"]
        # sanity: the endpoint returns the API-key shape with defaults
        assert "mortgage_rate" in before["value"]
        assert "petrol_cost_per_litre" in before["value"]

        resp = client.patch(
            "/api/settings/financial",
            json={"mortgage_rate": 0.06, "petrol_cost_per_litre": 1.60},
        )
        assert resp.status_code == 200

        after = client.get("/api/settings").json()["financial"]
        assert after["value"]["mortgage_rate"] == 0.06
        assert after["value"]["petrol_cost_per_litre"] == 1.60
        # MPG is per-person now, not a household finance
        assert "petrol_mpg" not in after["value"]
        # untouched nodes keep their defaults (serialized to float)
        assert after["value"]["sinking_fund_rate"] == float(SETTING_DEFAULTS["settings/sinking_fund_rate"][1]())
        assert aggregate_dict(get_services().setting_nodes) == after["value"]

    def test_put_persons_removed(self):
        """PUT /settings/persons (whole-list, no authz) must be gone."""
        client = self._setup()
        resp = client.put("/api/settings/persons", json=[{"name": "Simon", "has_car": True}])
        assert resp.status_code == 404, f"Expected 404 (endpoint removed), got {resp.status_code}"


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
                is_superuser=superuser,  # live settings are authoritative
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
                        "acceptable_modes": ["transit", "car"],
                    }
                ],
                "thresholds": {"good_max_minutes": 25, "fine_max_minutes": 40},
            },
        )
        assert resp.status_code == 200, resp.text[:300]
        simon = self._person(client, "Simon")
        poi = simon["places_of_interest"][0]
        assert poi["acceptable_modes"] == ["transit", "car"]
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
                "petrol_mpg": 40,
                "home_sale_price": {"amount": "550000", "currency": "GBP"},
            },
        )
        assert resp.status_code == 200, resp.text[:300]
        simon = self._person(client, "Simon")
        assert simon["bus_walk_penalty"] == {"value": 20, "unit": "minute"}
        assert simon["petrol_mpg"] == 40
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
        node.push([*list(node.latest_attempt().value_or_none() or []), {"name": "Legacy", "has_car": True}], "user")  # type: ignore[arg-type]  # push is typed with the declared value type (list[Person]); the legacy dict entry is the behavior under test — /api/settings must match persons by name and tolerate dict rows
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
                bus_walk_penalty=Quantity(10, "minute"),
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
        assert simon["places_of_interest"][0]["acceptable_modes"] == ["transit"]
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
                is_superuser=True,  # live settings are authoritative
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

    def test_whole_pounds_large_money_fields_reject_pence(self):
        """House-purchase amounts (sale/mortgage/cash) are whole pounds —
        the server REJECTS pence (400) rather than silently rounding;
        small monthly amounts still allow pence."""
        client = self._setup()
        for field in ("home_sale_price", "outstanding_mortgage", "cash_contribution"):
            resp = client.patch(
                "/api/settings/person/Ashby",
                json={"name": "Ashby", "has_car": True, field: {"amount": "300000.50", "currency": "GBP"}},
            )
            assert resp.status_code == 400, f"{field}: expected 400, got {resp.status_code}: {resp.text}"
            assert "whole number of pounds" in resp.json()["detail"], f"{field}: {resp.json()['detail']}"

        # pence are fine on the small monthly field
        resp = client.patch(
            "/api/settings/person/Ashby",
            json={"name": "Ashby", "has_car": True, "life_insurance_monthly": {"amount": "150.50", "currency": "GBP"}},
        )
        assert resp.status_code == 200, resp.text
        value = client.get("/api/settings").json()["persons"]["value"]
        ashby = next(p for p in value if p["name"] == "Ashby")
        assert ashby["life_insurance_monthly"]["amount"] == "150.50"

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

    def test_list_persons_returns_everyone_including_email_less(self):
        """The impersonation dropdown lists ALL persons — no email
        filter; callers that need only email-linked persons filter
        themselves (the server's no-children rule lives in the
        impersonate endpoint)."""
        client = self._setup()
        persons = client.get("/api/persons").json()["persons"]
        names = {p["name"]: p for p in persons}
        # email-less persons are present (Ashby has no email in defaults)
        assert "Ashby" in names
        assert names["Ashby"]["email"] == ""
        assert names["Ashby"]["is_child"] is False
        assert "Simon" in names
        assert "email" in names["Simon"]

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

    def test_deposit_splits_home_equity_by_co_owner_shares(self):
        """A declared home splits by home_co_owners: the holder keeps
        the remainder, each co-owner gets their share — total unchanged,
        attribution honest."""
        from decimal import Decimal as _Decimal

        from money import Money

        from houses.model.domain import HomeCoOwner, Person
        from houses.web.api_router import _deposit_breakdown

        persons = [
            Person(
                name="Simon",
                has_car=True,
                home_sale_price=Money("550000", "GBP"),
                outstanding_mortgage=Money("373000", "GBP"),
                cash_contribution=Money("0", "GBP"),
                home_co_owners=(HomeCoOwner(name="Lorena", share=50),),
            ),
            Person(
                name="Lorena",
                has_car=False,
                cash_contribution=Money("0", "GBP"),
            ),
            Person(
                name="Ashby",
                has_car=True,
                cash_contribution=Money("300000", "GBP"),
            ),
        ]
        breakdown = _deposit_breakdown(persons)
        deposit_persons, total, lines = breakdown.persons, breakdown.total, breakdown.lines
        assert deposit_persons["Simon"] == {"amount": "88500.00", "currency": "GBP"}
        assert deposit_persons["Lorena"] == {"amount": "88500.00", "currency": "GBP"}
        assert deposit_persons["Ashby"] == {"amount": "300000.00", "currency": "GBP"}
        # the total is unchanged: 177000 + 300000
        assert total.amount == _Decimal("477000.00")
        lines_by_label = {v["label"]: v["value"] for v in lines}
        assert "50% yours" in lines_by_label["Simon"]
        assert "50% of Simon's" in lines_by_label["Lorena"]

    def test_deposit_excludes_children_completely(self):
        """Children never appear in the deposit breakdown, provenance
        lines, or the total — even one with a stray cash contribution."""
        from decimal import Decimal as _Decimal

        from money import Money

        from houses.model.domain import Person
        from houses.web.api_router import _deposit_breakdown

        persons = [
            Person(
                name="Simon",
                has_car=True,
                home_sale_price=Money("550000", "GBP"),
                outstanding_mortgage=Money("373000", "GBP"),
                cash_contribution=Money("50000", "GBP"),
            ),
            Person(
                name="George",
                has_car=False,
                is_child=True,
                cash_contribution=Money("999999", "GBP"),  # must be ignored
            ),
        ]
        breakdown = _deposit_breakdown(persons)
        deposit_persons, total, lines = breakdown.persons, breakdown.total, breakdown.lines
        assert "George" not in deposit_persons
        assert "George" not in [line["label"] for line in lines]
        assert total.amount == _Decimal("227000.00")
        assert deposit_persons["Simon"] == {"amount": "227000.00", "currency": "GBP"}

    def test_settings_change_updates_property_totals(self):
        """PATCH a person's cash contribution → mortgage_required drops by
        exactly the delta and the list total follows — automatically, via
        the DAG.  One flush drains the WHOLE cascade (equity → mortgage →
        monthly payment → housing cost) deterministically; production's
        background processor does the same without test machinery."""
        from tests.unit.conftest import flush_all

        client = self._setup()
        rid = _seed_property()
        flush_all()  # one drain cascades the whole wave (see coding-standards)

        baseline = client.get(f"/api/properties/{rid}/detail").json()
        baseline_mortgage = baseline["affordability"]["mortgage_required"]
        assert baseline_mortgage is not None
        # capture the list baseline BEFORE the change (the summary and
        # detail read the same node — compare unwrapped values)
        baseline_total = Decimal(
            client.get("/api/properties/all").json()[rid]["group_monthly_cost"]["value"]["couple"]["value"]
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
        updated_group = client.get("/api/properties/all").json()[rid]["group_monthly_cost"]["value"]
        updated_total = Decimal(updated_group["couple"]["value"])
        assert updated_total < baseline_total, "list total did not decrease after the settings change"
        bm = _amount_of(baseline["affordability"]["monthly_mortgage"])
        um = _amount_of(updated["affordability"]["monthly_mortgage"])
        assert abs((baseline_total - updated_total) - (bm - um)) <= Decimal("0.01"), (
            "list total moved by the monthly-payment delta (within 0.01 rounding)"
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
        from houses.server import app
        from houses.services_provider import get_services

        _push_persons(
            Person(
                name="Simon",
                has_car=True,
                email="simon@example.com",
                is_superuser=True,  # live settings are authoritative
                home_sale_price=Money("550000", "GBP"),
                outstanding_mortgage=Money("373000", "GBP"),
            ),
            Person(name="Lorena", has_car=False, email="lorena@example.com"),
            Person(name="Ashby", has_car=True, cash_contribution=Money("300000", "GBP")),
        )
        registry = get_services().property_registry
        registry.clear()
        client = TestClient(app)
        _inject_session(client)
        return client, registry

    def _seed(self, reg):
        from money import Money

        from houses.geopoint import GeoPoint
        from houses.nodes.property_nodes import PropertyNodes

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
        reg.register("whatif1", prop)
        return prop

    def test_what_if_changes_totals_without_persisting(self):
        client, reg = self._setup()
        self._seed(reg)
        flush_all()

        baseline = client.get("/api/properties/all").json()["whatif1"]["group_monthly_cost"]
        base_couple = Decimal(baseline["value"]["couple"]["value"])

        # What-if: Ashby's cash contribution up £100k → equity up → the
        # mortgage (and so the couple's monthly total) must drop.
        resp = client.post(
            "/api/what-if",
            json={"persons": [{"name": "Ashby", "cash_contribution": {"amount": "400000", "currency": "GBP"}}]},
        )
        assert resp.status_code == 200
        result = resp.json()["results"]["whatif1"]
        assert result["succeeded"], result.get("error")
        hypothetical = Decimal(result["group"]["couple"]["value"])

        assert hypothetical < base_couple, "extra cash must lower the couple's monthly total"

        # Nothing persisted: the summary (and the real persons) are unchanged.
        after = client.get("/api/properties/all").json()["whatif1"]["group_monthly_cost"]
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

    def test_what_if_rejects_pence_on_whole_pound_fields(self):
        """What-if uses the same money rules as settings: pence on
        sale/mortgage/cash fail fast (400), never rounded."""
        client, reg = self._setup()
        self._seed(reg)
        flush_all()

        for field in ("home_sale_price", "outstanding_mortgage", "cash_contribution"):
            resp = client.post(
                "/api/what-if",
                json={"persons": [{"name": "Ashby", field: {"amount": "300000.50", "currency": "GBP"}}]},
            )
            assert resp.status_code == 400, f"{field}: expected 400, got {resp.status_code}: {resp.text}"
            assert "whole number of pounds" in resp.json()["detail"], f"{field}: {resp.json()['detail']}"


class TestRegenerateApi:
    """POST /api/admin/regenerate — force recompute of non-stale nodes."""

    def _setup(self):
        from fastapi.testclient import TestClient

        from houses.server import app
        from houses.services_provider import get_services

        registry = get_services().property_registry
        registry.clear()
        client = TestClient(app)
        return client, registry

    def _seed(self, reg):
        from money import Money

        from houses.geopoint import GeoPoint
        from houses.nodes.property_nodes import PropertyNodes

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
        reg.register("77777777", prop)
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
            Person(name="Simon", has_car=True, email="simon@example.com", is_superuser=True),
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
        total = prop.group_monthly_cost.latest_attempt()
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
        from houses.nodes.property_nodes import PropertyNodes
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

    def test_works_estimate_rejects_pence(self):
        """Works estimates are whole pounds — pence fail fast (400)."""
        from houses.nodes.property_nodes import PropertyNodes
        from houses.property_registry import register_property

        rid = "12345679"
        client = self._setup()
        register_property(rid, PropertyNodes(rid))

        resp = client.patch(
            f"/api/properties/{rid}/works-estimate",
            json={"person": "Ashby", "value": 15000.50},
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text[:300]}"
        assert "whole number" in resp.json()["detail"]

        # nothing was stored
        from houses.property_registry import get_property

        prop = get_property(rid)
        assert prop is not None
        assert prop.works_estimates.latest_attempt().value_or_none() is None

    def test_works_estimate_propagates_to_detail(self):
        """After PATCH, GET detail must show updated mortgage
        WITHOUT requiring an explicit flush."""
        from money import Money

        from houses.geopoint import GeoPoint
        from houses.nodes.property_nodes import PropertyNodes
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
