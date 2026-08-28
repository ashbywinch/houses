"""Add-property UX flow — the endpoints behind the wireframe's states.

Covers: the list summary carrying the real scrape state, retry (re-enqueue
of a permanently failed job), "I know the details" (PATCH with user facts
completes the property and cancels the queue job), and Remove (deletes the
job + the property's DAG rows + registry entry).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

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
        with inject_server_deps(
            scrape_fn=AsyncMock(
                return_value=AsyncMock(
                    address="Penwood Lane, Marlow, SL7 2AP",
                    postcode="SL7 2AP",
                    bedrooms=4,
                    price=800000,
                    latitude=51.5676,
                    longitude=-0.7842,
                    url=URL,
                )
            )
        ):
            client.post("/api/properties", json={"url": URL})
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
        detail = client.get(f"/api/properties/{RID}").json()
        assert detail["best_address"]["value"] == "Penwood Lane, Marlow, SL7 2AP"
        assert detail["rightmove_price"]["value"]["amount"] == "650000.00"


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
