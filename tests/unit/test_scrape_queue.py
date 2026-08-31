"""Scrape queue — durable retry with exponential backoff for Rightmove scrapes.

The cloud box has no Chrome; property adds there enqueue scrape jobs, a
worker (LAN, where Chrome exists) claims + scrapes + reports, and failed
scrapes are re-queued with exponential backoff by the app. These tests pin
the queue contract: enqueue on failed scrape, claim-once semantics,
backoff growth, permanent failure after MAX_ATTEMPTS, stale-claim
recovery, success applies scraped data to the DAG, auth.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from houses.database import get_connection
from houses.scrape_queue import MAX_ATTEMPTS, STALE_CLAIM_SECONDS
from houses.server import app
from houses.web.auth import _make_session_cookie

client = TestClient(app)
client.cookies.set(
    "session",
    _make_session_cookie(email="simon@example.com", name="Simon", picture="", is_superuser=True),
)

URL = "https://www.rightmove.co.uk/properties/89498715"
RID = "89498715"


def _now() -> datetime:
    return datetime.now(UTC)


# lucidlint: ignore record-shape wire-format dict — sqlite row projection is
# the serialization boundary (coding-standards.md)
def _scrape_rows() -> list[dict]:
    conn = get_connection()
    return [dict(r) for r in conn.execute("SELECT * FROM pending_scrapes ORDER BY id").fetchall()]


# lucidlint: ignore record-shape wire-format dict — the claim response is a wire record (coding-standards.md)
def _add_url_only() -> dict:
    """Add a URL-only property with a scrape that returns nothing; returns
    the claim response's job dict."""
    client.post("/api/properties", json={"url": URL})
    return client.post("/api/scrapes/claim").json()["job"]


def _fail_job_until_permanent(job_id: int) -> None:
    for _ in range(MAX_ATTEMPTS):
        client.post("/api/scrapes/report", json={"job_id": job_id, "ok": False, "error": "boom"})
        client.post("/api/scrapes/claim")


class TestEnqueueOnFailedScrape:
    def test_url_only_add_is_instant_and_enqueues(self):
        """A URL-only add must NEVER block on the scrape — the request
        returns immediately with scrape_pending and the queue owns the
        work (regression: the add hung for the scrape on environments
        with a local scraper)."""
        resp = client.post("/api/properties", json={"url": URL})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("scrape_pending") is True, body
        rows = _scrape_rows()
        assert len(rows) == 1
        assert rows[0]["rid"] == RID
        assert rows[0]["url"] == URL
        assert rows[0]["attempts"] == 0
        assert rows[0]["status"] == "pending"

    def test_address_payload_add_does_not_enqueue(self):
        """A payload WITH the user's own facts is seeded directly — no
        scrape, no enqueue (the scrape is for URL-only adds)."""
        resp = client.post(
            "/api/properties",
            json={"url": URL, "address": "Penwood Lane, Marlow, SL7 2AP", "price": 650000},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json().get("scrape_pending") is None
        assert _scrape_rows() == []

    def test_duplicate_enqueue_is_skipped(self):
        """Re-adding the same property while a job is active must not
        create a second job."""
        client.post("/api/properties", json={"url": URL})
        client.post("/api/properties", json={"url": URL})
        assert len(_scrape_rows()) == 1


class TestClaim:
    def test_claim_returns_due_job_and_marks_in_progress(self):
        client.post("/api/properties", json={"url": URL})
        resp = client.post("/api/scrapes/claim")
        assert resp.status_code == 200, resp.text
        job = resp.json()["job"]
        assert job["rid"] == RID
        assert job["url"] == URL
        rows = _scrape_rows()
        assert rows[0]["status"] == "in_progress"
        assert rows[0]["claimed_at"] is not None
        # A claimed job is not claimable again
        resp2 = client.post("/api/scrapes/claim")
        assert resp2.json()["job"] is None

    def test_claim_respects_next_retry_at(self):
        """A job whose backoff window has not elapsed must not be claimable."""
        job = _add_url_only()
        client.post("/api/scrapes/report", json={"job_id": job["id"], "ok": False, "error": "login wall"})
        assert client.post("/api/scrapes/claim").json()["job"] is None
        # Force the backoff window to pass — the job becomes claimable again
        conn = get_connection()
        conn.execute(
            "UPDATE pending_scrapes SET next_retry_at=? WHERE id=?",
            (datetime.now(UTC).isoformat(), job["id"]),
        )
        conn.commit()
        job2 = client.post("/api/scrapes/claim").json()["job"]
        assert job2 is not None and job2["id"] == job["id"]

    @staticmethod
    def test_status_reads_the_newest_job_for_a_rid_with_several():
        """A permanently failed job plus a re-enqueued one must report
        the NEWEST job — an unordered read showed the stale 'failed'
        state on the card (PR #68 review, Medium)."""
        from houses.scrape_queue import enqueue_scrape, scrape_status_for_rid

        job = _add_url_only()
        conn = get_connection()
        conn.execute("UPDATE pending_scrapes SET status='failed' WHERE id=?", (job["id"],))
        conn.commit()
        # failed is not an active status — the retry enqueues a second row
        assert enqueue_scrape(job["rid"], job["url"]) is True
        status = scrape_status_for_rid(job["rid"])
        assert status is not None and status.status == "pending", (
            "must report the re-enqueued job, not the stale failed row"
        )
        assert status.attempts == 0

    @staticmethod
    def test_failed_job_is_never_claimable_again():
        """Review-bug regression: after MAX_ATTEMPTS the job is failed
        PERMANENTLY — a stale next_retry_at must not make it claimable
        again, or the queue retries failed jobs forever."""
        job = _add_url_only()
        _fail_job_until_permanent(job["id"])
        # Force the stale retry time into the past — the claim filter must
        # still exclude it (status is 'failed', not 'pending').
        conn = get_connection()
        conn.execute(
            "UPDATE pending_scrapes SET next_retry_at=? WHERE id=?",
            (datetime(2020, 1, 1, tzinfo=UTC).isoformat(), job["id"]),
        )
        conn.commit()
        assert client.post("/api/scrapes/claim").json()["job"] is None

    @staticmethod
    def test_stale_in_progress_job_is_reclaimed_with_backoff():
        """Review-bug regression: a worker that dies after claiming (never
        reports) must not stall the property forever — the abandoned job
        is requeued counting as an attempt, with the backoff window, so
        it is NOT instantly re-claimed."""
        job = _add_url_only()
        conn = get_connection()
        conn.execute(
            "UPDATE pending_scrapes SET claimed_at=? WHERE id=?",
            ((_now() - timedelta(seconds=STALE_CLAIM_SECONDS * 2)).isoformat(), job["id"]),
        )
        conn.commit()
        assert client.post("/api/scrapes/claim").json()["job"] is None, (
            "a stale-reclaimed job must back off, not be immediately claimable"
        )
        rows = _scrape_rows()
        assert rows[0]["status"] == "pending"
        assert rows[0]["attempts"] == 1
        assert datetime.fromisoformat(rows[0]["next_retry_at"]) > _now()

    @staticmethod
    def test_repeated_stale_reclaims_converge_to_failed():
        """A worker that keeps dying before reporting must not loop
        forever — stale reclaims count toward MAX_ATTEMPTS and the job
        reaches the failed terminal state the card's Retry expects
        (PR #68 review)."""
        job = _add_url_only()
        conn = get_connection()
        for _ in range(MAX_ATTEMPTS - 1):
            conn.execute(
                "UPDATE pending_scrapes SET claimed_at=? WHERE id=?",
                ((_now() - timedelta(seconds=STALE_CLAIM_SECONDS * 2)).isoformat(), job["id"]),
            )
            conn.commit()
            # reclaim: requeued with attempts+1 + backoff → nothing due yet
            assert client.post("/api/scrapes/claim").json()["job"] is None
            conn.execute(
                "UPDATE pending_scrapes SET next_retry_at=? WHERE id=?",
                (_now().isoformat(), job["id"]),
            )
            conn.commit()
            job2 = client.post("/api/scrapes/claim").json()["job"]
            assert job2 is not None and job2["id"] == job["id"]
        # The final stale reclaim crosses MAX_ATTEMPTS → permanently failed.
        conn.execute(
            "UPDATE pending_scrapes SET claimed_at=? WHERE id=?",
            ((_now() - timedelta(seconds=STALE_CLAIM_SECONDS * 2)).isoformat(), job["id"]),
        )
        conn.commit()
        assert client.post("/api/scrapes/claim").json()["job"] is None
        rows = _scrape_rows()
        assert rows[0]["status"] == "failed"
        assert rows[0]["attempts"] == MAX_ATTEMPTS

class TestBackoff:
    def test_failure_backs_off_exponentially(self):
        job = _add_url_only()
        delays = []
        for _ in range(3):
            client.post("/api/scrapes/report", json={"job_id": job["id"], "ok": False, "error": "boom"})
            rows = _scrape_rows()
            retry_at = datetime.fromisoformat(rows[0]["next_retry_at"])
            delays.append((retry_at - _now()).total_seconds())
            assert rows[0]["attempts"] == len(delays)
            client.post("/api/scrapes/claim")  # re-claim for the next failure
        assert delays[0] > 0
        assert delays[1] > delays[0]
        assert delays[2] > delays[1]

    def test_max_attempts_marks_job_failed_permanently(self):
        job = _add_url_only()
        _fail_job_until_permanent(job["id"])
        rows = _scrape_rows()
        assert rows[0]["status"] == "failed"
        assert rows[0]["attempts"] == MAX_ATTEMPTS
        assert client.post("/api/scrapes/claim").json()["job"] is None


class TestReportSuccess:
    def test_success_applies_scraped_data_and_removes_job(self):
        """A successful report must delete the job and push the scraped
        values into the DAG (address, postcode, bedrooms, price)."""
        job = _add_url_only()
        resp = client.post(
            "/api/scrapes/report",
            json={
                "job_id": job["id"],
                "ok": True,
                "data": {
                    "address": "Penwood Lane, Marlow, SL7 2AP",
                    "postcode": "SL7 2AP",
                    "bedrooms": 4,
                    "price": 800000,
                    "latitude": 51.5676,
                    "longitude": -0.7842,
                },
            },
        )
        assert resp.status_code == 200, resp.text
        assert _scrape_rows() == []
        # The DAG now carries the scraped values
        detail = client.get(f"/api/properties/{RID}").json()
        assert detail["best_address"]["value"] == "Penwood Lane, Marlow, SL7 2AP"
        assert detail["postcode"]["value"] == "SL7 2AP"
        assert detail["rightmove_bedrooms"]["value"] == "4"
        assert detail["rightmove_price"]["value"]["amount"] == "800000.00"

    @staticmethod
    def test_success_without_address_is_rejected():
        """Review-bug regression: a login wall / block page can parse with
        an empty address — the server must NOT delete the job and seed an
        empty property; it re-queues the scrape instead."""
        job = _add_url_only()
        resp = client.post(
            "/api/scrapes/report",
            json={"job_id": job["id"], "ok": True, "data": {"address": "", "bedrooms": 4, "price": 500000}},
        )
        assert resp.status_code == 200, resp.text
        rows = _scrape_rows()
        assert len(rows) == 1, "empty-address report must not delete the job"
        assert rows[0]["attempts"] == 1
        # The property must not have been seeded with garbage
        detail = client.get(f"/api/properties/{RID}").json()
        assert detail["best_address"]["value"] in (None, "")

    @staticmethod
    def test_report_missing_job_id_is_422():
        resp = client.post("/api/scrapes/report", json={"ok": True, "data": {"address": "x"}})
        assert resp.status_code == 422


class TestAuth:
    def test_claim_requires_superuser(self):
        # The auth middleware 401s unauthenticated /api/* calls
        anon = TestClient(app)
        assert anon.post("/api/scrapes/claim").status_code == 401
        # A signed-in non-superuser gets 403 from the endpoint
        non_super = TestClient(app)
        non_super.cookies.set(
            "session",
            _make_session_cookie(email="george@example.com", name="George", picture="", is_superuser=False),
        )
        assert non_super.post("/api/scrapes/claim").status_code == 403

class TestMalformedReport:
    @staticmethod
    def test_malformed_report_values_requeue_without_500():
        """A report whose bedrooms/price is unparseable must re-queue the
        job with a clear error — not 500 and leave it in_progress (PR #68
        review)."""
        job = _add_url_only()
        resp = client.post(
            "/api/scrapes/report",
            json={
                "job_id": job["id"],
                "ok": True,
                "data": {"address": "Somewhere", "bedrooms": "4 bed"},
            },
        )
        assert resp.status_code == 200, resp.text
        rows = _scrape_rows()
        assert rows[0]["status"] == "pending", "unparseable fields must re-queue"
        assert rows[0]["attempts"] == 1, "the failure must count as an attempt (backoff)"
