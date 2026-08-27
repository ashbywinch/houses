"""Durable Rightmove scrape queue — retried with exponential backoff.

The cloud box has no Chrome; property adds there enqueue scrape jobs and a
worker (tools/scrape_worker.py, running where Chrome exists — the LAN)
claims, scrapes, and reports back. Failed scrapes are re-queued with
exponential backoff so a transient Rightmove hiccup (login wall, timeout)
doesn't hammer the site. Retry state lives in SQLite — it survives worker
restarts and box restarts, which an in-memory queue cannot.

The backoff shape (base 60s, x2 per attempt, 1h cap) mirrors the DAG's
retry policy (dag/derived_node.py::_retry_delay_from — base 10s, x2,
300s cap): same exponential family, scaled up because scraping a third
party's site is more rate-sensitive than the API calls the DAG retries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from houses.database import get_connection

MAX_ATTEMPTS = 8
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 3600

# pending -> claimable
# in_progress -> claimed, awaiting report (or stale if the worker died)
# failed -> gave up after MAX_ATTEMPTS; not claimable, visible via status
_ACTIVE_STATUSES = ("pending", "in_progress")


@dataclass(frozen=True)
class ScrapeJob:
    """A claimed scrape job handed to the worker."""

    id: int
    rid: str
    url: str


@dataclass(frozen=True)
class ScrapeQueueStatus:
    """Per-status counts for operator visibility."""

    pending: int = 0
    in_progress: int = 0
    failed: int = 0


def backoff_delay(attempts: int) -> timedelta:
    """Exponential backoff for the attempt number (1-based)."""
    return timedelta(seconds=min(BACKOFF_BASE_SECONDS * (2**attempts), BACKOFF_CAP_SECONDS))


def enqueue_scrape(rid: str, url: str) -> bool:
    """Queue a scrape job; no-op if one is already active for the rid."""
    conn = get_connection()
    active = conn.execute(
        f"SELECT id FROM pending_scrapes WHERE rid=? AND status IN {_ACTIVE_STATUSES}",
        (rid,),
    ).fetchone()
    if active is not None:
        return False
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO pending_scrapes (rid, url, attempts, next_retry_at, status, created_at)"
        " VALUES (?, ?, 0, ?, 'pending', ?)",
        (rid, url, now, now),
    )
    conn.commit()
    return True


def claim_due_scrape() -> ScrapeJob | None:
    """Claim the oldest job whose backoff window has elapsed.

    Claimed jobs are marked in_progress and cannot be claimed again until
    the worker reports.  Returns None when nothing is due.
    """
    conn = get_connection()
    now = datetime.now(UTC).isoformat()
    row = conn.execute(
        "SELECT id, rid, url FROM pending_scrapes"
        " WHERE next_retry_at <= ? AND status IN ('pending', 'failed')"
        " ORDER BY created_at ASC LIMIT 1",
        (now,),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE pending_scrapes SET status='in_progress', claimed_at=? WHERE id=?",
        (now, row["id"]),
    )
    conn.commit()
    return ScrapeJob(id=row["id"], rid=row["rid"], url=row["url"])


def report_scrape(job_id: int, ok: bool, error: str | None = None) -> str | None:
    """Record a worker's outcome for a claimed job.

    Success: the job is deleted and the rid is returned (the caller applies
    the scraped data to the DAG).  Failure: attempts is incremented and the
    job is re-queued at now + backoff(attempts); after MAX_ATTEMPTS it is
    marked ``failed`` permanently.  Returns None when the job is unknown
    or the outcome was a failure.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT attempts, rid FROM pending_scrapes WHERE id=?", (job_id,)
    ).fetchone()
    if row is None:
        return None
    if ok:
        conn.execute("DELETE FROM pending_scrapes WHERE id=?", (job_id,))
        conn.commit()
        return row["rid"]
    attempts = row["attempts"] + 1
    if attempts >= MAX_ATTEMPTS:
        conn.execute(
            "UPDATE pending_scrapes SET attempts=?, status='failed', last_error=? WHERE id=?",
            (attempts, error, job_id),
        )
    else:
        retry_at = (datetime.now(UTC) + backoff_delay(attempts)).isoformat()
        conn.execute(
            "UPDATE pending_scrapes SET attempts=?, status='pending', next_retry_at=?,"
            " last_error=? WHERE id=?",
            (attempts, retry_at, error, job_id),
        )
    conn.commit()
    return None


def scrape_queue_status() -> ScrapeQueueStatus:
    """Counts by status for operator visibility."""
    conn = get_connection()
    counts: dict[str, int] = {}
    for (status, n) in conn.execute(
        "SELECT status, COUNT(*) FROM pending_scrapes GROUP BY status"
    ).fetchall():
        counts[status] = n
    return ScrapeQueueStatus(
        pending=counts.get("pending", 0),
        in_progress=counts.get("in_progress", 0),
        failed=counts.get("failed", 0),
    )
