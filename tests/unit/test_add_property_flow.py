"""Add-property UX flow — the endpoints behind the wireframe's states.

Covers: the list summary carrying the real scrape state, retry (re-enqueue
of a permanently failed job), "I know the details" (PATCH with user facts
completes the property and cancels the queue job), and Remove (deletes the
job + the property's DAG rows + registry entry).
"""

import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from dag.persistence import decompress_result
from houses.database import get_connection
from houses.scrape_queue import MAX_ATTEMPTS
from houses.server import app
from houses.services_provider import get_services
from houses.web.auth import (
    # lucidlint: ignore private-import shared-secret cookie mint — the only
    # minting entry point; same pattern as tools/deploy/release.sh
    _make_session_cookie,
)
from tests.helpers import inject_server_deps

client = TestClient(app)
client.cookies.set(
    "session",
    _make_session_cookie(email="simon@example.com", name="Simon", picture="", is_superuser=True),
)

URL = "https://www.rightmove.co.uk/properties/89498715"
RID = "89498715"


# lucidlint: ignore record-shape wire-format dict — sqlite row projection is
# the serialization boundary (coding-standards.md)
def _scrape_rows() -> list[dict]:
    conn = get_connection()
    return [dict(r) for r in conn.execute("SELECT * FROM pending_scrapes ORDER BY id").fetchall()]


def _add_url_only() -> None:
    """Add a URL-only property whose scrape yields nothing (job enqueued)."""
    with inject_server_deps(scrape_fn=AsyncMock(return_value=None)):
        client.post("/api/properties", json={"url": URL})


class TestSummaryCarriesScrapeState:
    @staticmethod
    def test_list_summary_includes_scrape_status():
        """The wireframe's card states come from the real queue: the list
        summary must carry the scrape job state for a pending property."""
        _add_url_only()
        all_props = client.get("/api/properties/all").json()
        assert RID in all_props
        assert all_props[RID]["scrape"]["status"] == "pending"

    @staticmethod
    def test_no_scrape_key_when_no_job():
        """A payload WITH the user's own facts never enqueues — no scrape
        state on the summary."""
        with inject_server_deps(scrape_fn=AsyncMock()):
            client.post("/api/properties", json={"url": URL, "address": "Penwood Lane, Marlow, SL7 2AP"})
        all_props = client.get("/api/properties/all").json()
        assert "scrape" not in all_props[RID]


class TestRetry:
    @staticmethod
    def test_retry_re_enqueues_a_permanently_failed_job():
        """A failed job is permanently dead — retry must re-enqueue it so
        the worker can try again (the wireframe's Retry button)."""
        _add_url_only()
        job = client.post("/api/scrapes/claim").json()["job"]
        for _ in range(MAX_ATTEMPTS):
            client.post("/api/scrapes/report", json={"job_id": job["id"], "ok": False, "error": "boom"})
            client.post("/api/scrapes/claim")
        assert _scrape_rows()[0]["status"] == "failed"

        resp = client.post(f"/api/properties/{RID}/scrape/retry")
        assert resp.status_code == 200, resp.text
        rows = _scrape_rows()
        assert rows[0]["status"] == "pending"
        assert rows[0]["attempts"] == 0


class TestManualDetails:
    @staticmethod
    def test_details_patch_completes_property_and_cancels_job():
        """'I know the details' — the user's own facts complete the
        property instantly and the now-unneeded scrape job is cancelled."""
        _add_url_only()
        resp = client.patch(
            f"/api/properties/{RID}/details",
            json={"address": "Penwood Lane, Marlow, SL7 2AP", "price": 650000, "bedrooms": 4},
        )
        assert resp.status_code == 200, resp.text
        assert _scrape_rows() == [], "manual details must cancel the scrape job"
        detail = client.get(f"/api/properties/{RID}/detail").json()
        assert detail["best_address"]["value"] == "Penwood Lane, Marlow, SL7 2AP"
        assert detail["rightmove_price"]["value"]["amount"] == "650000.00"

    @staticmethod
    def test_details_patch_seeds_the_postcode_from_the_address():
        """The typed address embeds a postcode — the postcode node must
        resolve so postcode-derived nodes (park_and_ride) can compute
        instead of staying permanently pending (PR #68 review, Medium)."""
        _add_url_only()
        resp = client.patch(
            f"/api/properties/{RID}/details",
            json={"address": "Penwood Lane, Marlow, SL7 2AP", "price": 650000, "bedrooms": 4},
        )
        assert resp.status_code == 200, resp.text
        prop = get_services().property_registry.get(RID)
        a = prop.postcode.latest_attempt()
        assert a.succeeded and a.value_or_none() == "SL7 2AP", (
            "the postcode embedded in the typed address must be seeded into the DAG"
        )

    @staticmethod
    def test_address_only_details_do_not_seed_fake_zeros():
        """The user fills only the address — the property must not
        display made-up £0/0 figures as real data, and the scrape job
        must survive to fill the missing facts (PR #68 review)."""
        _add_url_only()
        resp = client.patch(
            f"/api/properties/{RID}/details",
            json={"address": "Penwood Lane, Marlow, SL7 2AP"},
        )
        assert resp.status_code == 200, resp.text
        prop = get_services().property_registry.get(RID)
        assert prop.rightmove_price.latest_attempt().pending, (
            "an omitted price must not be seeded as £0"
        )
        assert prop.rightmove_bedrooms.latest_attempt().pending, (
            "omitted bedrooms must not be seeded as 0"
        )
        assert _scrape_rows() != [], (
            "the scrape job must survive while facts are still missing"
        )


class TestCommuteComputesAfterDetails:
    @staticmethod
    def test_transit_recomputes_once_the_address_arrives():
        """Regression (property 90691101): a URL-only add whose address
        arrives LATER (manual details / scrape report) must trigger the
        transit commute to COMPUTE — the DAG's contract is that
        dependents recompute when new info arrives, with no extra wiring.
        (The test env's TfL fake returns impossible; the point is the
        node must run at all — the box showed it never did, a NO-ROW.)"""
        _add_url_only()
        resp = client.patch(
            f"/api/properties/{RID}/details",
            json={"address": "Penwood Lane, Marlow, SL7 2AP", "price": 650000, "bedrooms": 4},
        )
        assert resp.status_code == 200, resp.text
        conn = get_connection()
        row = conn.execute(
            "SELECT result_json FROM node_results WHERE node_id=? ORDER BY rowid DESC LIMIT 1",
            (f"{RID}/Simon/Pimlico/computed_transit",),
        ).fetchone()
        assert row is not None, (
            "the transit commute must be computed once the address arrives — "
            "the DAG must auto-recompute dependents of the new location"
        )
        d = json.loads(decompress_result(row[0]))
        assert d["status"] != "pending", "transit must have RUN, not stayed pending"

    @staticmethod
    def test_no_route_error_is_user_facing():
        """The 'Choose: no alternative selected' error must read as plain
        language in the UI — no internal expression jargon."""
        _add_url_only()
        client.patch(
            f"/api/properties/{RID}/details",
            json={"address": "Penwood Lane, Marlow, SL7 2AP", "price": 650000, "bedrooms": 4},
        )
        detail = client.get(f"/api/properties/{RID}/detail").json()
        commutes = detail.get("commutes", {})
        assert commutes
        for _key, entry in commutes.items():
            c = entry.get("commute", entry)
            if c.get("status") == "impossible":
                err = (c.get("error") or "").lower()
                assert "choose" not in err, f"internal jargon leaked: {c.get('error')}"
                assert "alternative" not in err, f"internal jargon leaked: {c.get('error')}"


class TestRemove:
    @staticmethod
    def test_remove_deletes_job_dag_rows_and_registry():
        """Remove on a waiting card: the queue job, the property's DAG
        rows, and the registry entry all go away."""
        _add_url_only()
        conn = get_connection()
        before = conn.execute(
            "SELECT COUNT(*) FROM node_results WHERE node_id LIKE ?", (f"{RID}/%",)
        ).fetchone()[0]
        assert before > 0, "the add must have seeded DAG rows"

        resp = client.delete(f"/api/properties/{RID}")
        assert resp.status_code == 200, resp.text
        assert _scrape_rows() == []
        after = conn.execute(
            "SELECT COUNT(*) FROM node_results WHERE node_id LIKE ?", (f"{RID}/%",)
        ).fetchone()[0]
        assert after == 0, "DAG rows must be removed"
        assert get_services().property_registry.get(RID) is None
        assert client.get(f"/api/properties/{RID}").status_code == 404

    @staticmethod
    def test_remove_disconnects_the_dag_nodes():
        """Remove must drop the property's nodes from the scheduler — a
        re-add of the same URL must not collide with orphaned nodes
        registered under the same ids (their queued events would clobber
        the re-added property's rows; PR #68 review)."""
        from dag.scheduler import get_scheduler

        _add_url_only()
        prefix = f"{RID}/"
        assert any(nid.startswith(prefix) for nid in get_scheduler().registered_nodes())
        resp = client.delete(f"/api/properties/{RID}")
        assert resp.status_code == 200, resp.text
        assert not any(nid.startswith(prefix) for nid in get_scheduler().registered_nodes()), (
            "the removed property's nodes must be unregistered from the scheduler"
        )

    @staticmethod
    def test_remove_then_readd_computes():
        """Remove then re-add the same listing — the fresh PropertyNodes
        must compute, not starve behind orphaned nodes (PR #68 review)."""
        _add_url_only()
        assert client.delete(f"/api/properties/{RID}").status_code == 200
        _add_url_only()
        job = client.post("/api/scrapes/claim").json()["job"]
        client.post(
            "/api/scrapes/report",
            json={
                "job_id": job["id"],
                "ok": True,
                "data": {"address": "Penwood Lane, Marlow, SL7 2AP"},
            },
        )
        prop = get_services().property_registry.get(RID)
        assert prop is not None
        a = prop.postcode.latest_attempt()
        assert a.succeeded and a.value_or_none() == "SL7 2AP", (
            "a re-added property must compute like a fresh one"
        )

class TestAddressPatchDerivesPostcode:
    @staticmethod
    def test_editing_the_address_to_add_a_postcode_resolves_it():
        """A URL-only add has no postcode; the user edits the address to
        add one — the DAG derives it (PostcodeNode), no endpoint
        coordination (the blank-postcode bug that started this thread)."""
        _add_url_only()
        resp = client.patch(
            f"/api/properties/{RID}/address",
            json={"address": "Penwood Lane, Marlow, SL7 2AP"},
        )
        assert resp.status_code == 200, resp.text
        prop = get_services().property_registry.get(RID)
        a = prop.postcode.latest_attempt()
        assert a.succeeded and a.value_or_none() == "SL7 2AP", (
            "the postcode node must derive from the edited address"
        )

    @staticmethod
    def test_edit_overrides_the_scraped_postcode():
        """The scrape lands 'Moores Place, Hungerford, RG17 0LA'; the
        user edits the address to a different one — the postcode must
        follow the EDITED address, not stay with Rightmove's."""
        _add_url_only()
        job = client.post("/api/scrapes/claim").json()["job"]
        client.post(
            "/api/scrapes/report",
            json={
                "job_id": job["id"],
                "ok": True,
                "data": {"address": "Moores Place, Hungerford, RG17 0LA"},
            },
        )
        resp = client.patch(
            f"/api/properties/{RID}/address",
            json={"address": "Penwood Lane, Marlow, SL7 2AP"},
        )
        assert resp.status_code == 200, resp.text
        prop = get_services().property_registry.get(RID)
        a = prop.postcode.latest_attempt()
        assert a.succeeded and a.value_or_none() == "SL7 2AP", (
            "the user's edited address must override Rightmove's postcode"
        )


class TestPostcodeOnlyPayload:
    @pytest.mark.asyncio
    async def test_postcode_without_address_is_not_lost(self):
        """An API client sending a postcode with no address must not
        lose it — the postcode seeds the provisional address until the
        scrape lands (PR #68 review, data loss)."""
        from dag.scheduler import flush_processor

        with inject_server_deps(scrape_fn=AsyncMock(return_value=None)):
            resp = client.post("/api/properties", json={"url": URL, "postcode": "SL7 2AP"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["postcode"] == "SL7 2AP"
        await flush_processor()
        prop = get_services().property_registry.get(RID)
        a = prop.postcode.latest_attempt()
        assert a.succeeded and a.value_or_none() == "SL7 2AP", (
            "a postcode sent without an address must still reach the DAG"
        )
