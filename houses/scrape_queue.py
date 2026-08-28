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
STALE_CLAIM_SECONDS = 900
"""An in_progress job older than this (worker died before reporting) is
re-queued so the property's enrichment doesn't stall forever."""
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
class ScrapeJobStatus:
    """One property's honest queue state (the card's states)."""

    status: str
    attempts: int
    created_at: str
    claimed_at: str | None
    next_retry_at: str
    last_error: str | None


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
    """Claim the oldest due job whose backoff window has elapsed.

    First, abandoned claims are re-queued: an in_progress job whose
    claimed_at is older than STALE_CLAIM_SECONDS (a worker died before
    reporting) goes back to pending so its property isn't stalled
    forever. Then the oldest pending job past its retry time is claimed.

    Only ``pending`` jobs are claimable — a job permanently failed after
    MAX_ATTEMPTS must NEVER come back (a stale next_retry_at must not
    resurrect it).  Returns None when nothing is due.
    """
    conn = get_connection()
    now = datetime.now(UTC).isoformat()
    stale_before = (datetime.now(UTC) - timedelta(seconds=STALE_CLAIM_SECONDS)).isoformat()
    conn.execute(
        "UPDATE pending_scrapes SET status='pending', next_retry_at=?, claimed_at=NULL"
        " WHERE status='in_progress' AND claimed_at IS NOT NULL AND claimed_at <= ?",
        (now, stale_before),
    )
    row = conn.execute(
        "SELECT id, rid, url FROM pending_scrapes"
        " WHERE next_retry_at <= ? AND status = 'pending'"
        " ORDER BY created_at ASC LIMIT 1",
        (now,),
    ).fetchone()
    # lucidlint: ignore special-case sqlite row absence — a missing job row IS the
    # absent case; a null-object dataclass would add ceremony for zero benefit
    if row is None:
        conn.commit()  # persist the stale-claim requeue even when nothing is due
        return None
    conn.execute(
        "UPDATE pending_scrapes SET status='in_progress', claimed_at=? WHERE id=?",
        (now, row["id"]),
    )
    conn.commit()
    return ScrapeJob(id=row["id"], rid=row["rid"], url=row["url"])


def scrape_job_rid(job_id: int) -> str | None:
    """Look up a claimed job's rid WITHOUT mutating it.

    The report endpoint applies the scraped data to the DAG before
    deleting the job, so a failed apply can re-queue instead of losing
    the listing forever.
    """
    conn = get_connection()
    row = conn.execute("SELECT rid FROM pending_scrapes WHERE id=?", (job_id,)).fetchone()
    return row["rid"] if row is not None else None
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


def scrape_status_for_rid(rid: str) -> ScrapeJobStatus | None:
    """The job row's honest state for one property, or None when no job
    exists. The card derives its message from these REAL fields — status
    is pending (queued, not yet claimed), in_progress (a worker is
    scraping NOW), or failed (gave up); created_at/claimed_at let the UI
    show how long the wait has been without a fake timer."""
    conn = get_connection()
    row = conn.execute(
        "SELECT status, attempts, created_at, claimed_at, next_retry_at, last_error"
        " FROM pending_scrapes WHERE rid=?",
        (rid,),
    ).fetchone()
    if row is None:
        return None
    return ScrapeJobStatus(
        status=row["status"],
        attempts=row["attempts"],
        created_at=row["created_at"],
        claimed_at=row["claimed_at"],
        next_retry_at=row["next_retry_at"],
        last_error=row["last_error"],
    )


def cancel_scrape_for_rid(rid: str) -> bool:
    """Drop any pending/in_progress/failed job for a property (manual
    details completed it, or the user removed it)."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM pending_scrapes WHERE rid=?", (rid,))
    conn.commit()
    return cur.rowcount > 0


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
