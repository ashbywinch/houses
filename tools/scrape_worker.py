#!/usr/bin/env python
"""Rightmove scrape worker — runs WHERE CHROME EXISTS (the LAN machine).

The cloud box has no Chrome; property adds there enqueue scrape jobs with
exponential backoff (houses/scrape_queue.py). This worker claims due jobs
from the app, scrapes with the repo's own scraper (headless Chrome on this
machine), and reports the outcome back. Failed scrapes are re-queued by the
app — the worker never retries, the queue's backoff gates when a job becomes
claimable again.

Auth: mints a superuser session cookie from HOUSES_SESSION_SECRET. The LAN
.env and the cloud box's /etc/houses.env share the same secret, so a cookie
minted here is valid there (pydantic-settings loads the LAN .env at import).

Usage:
    HOUSES_SCRAPE_APP_URL=https://houses.blueumbrella.net \
        .venv/bin/python tools/scrape_worker.py --once
    .venv/bin/python tools/scrape_worker.py --loop --interval 60

Run --once on a systemd timer / cron every few minutes, or --loop as a
background service. ``--once`` processes one due job, then exits.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

# Run as `python tools/scrape_worker.py`, sys.path[0] is tools/, not the repo
# root — and the venv's editable finder only maps the top-level packages that
# existed when it was built. That is how `serialization` (added later) went
# missing and both units crash-looped: each 5-second restart hit ImportError
# immediately. Put the root first, before any repo import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dag.guard import capture_async, guarded_async
from houses.rightmove_scraper import scrape

# lucidlint: ignore private-import cookie mint — only minting entry point; mirrors tools/deploy/install-artifact.sh
from houses.web.auth import _make_session_cookie


@dataclass(frozen=True)
class ScrapeJob:
    """A claimed job as the worker sees it (wire record from the API)."""

    id: int
    rid: str
    url: str


@dataclass(frozen=True)
class ScrapedListing:
    """The scraped data the worker reports back."""

    url: str
    address: str
    postcode: str
    bedrooms: int | None
    price: float | None
    latitude: float | None
    longitude: float | None


def _headers() -> dict[str, str]:
    cookie = _make_session_cookie(
        email=os.environ.get("HOUSES_SCRAPE_EMAIL", "simon@example.com"),
        name="Simon",
        picture="",
        is_superuser=True,
    )
    return {"Cookie": f"session={cookie}"}


def _app_url() -> str:
    url = os.environ.get("HOUSES_SCRAPE_APP_URL")
    if not url:
        raise SystemExit("HOUSES_SCRAPE_APP_URL is required (the app's public URL)")
    return url.rstrip("/")


class _ClaimedJob:
    """A claimed job and the app client that scrapes and reports it.

    The (client, job) pair is one thing in this worker's domain — the job
    being worked — so it travels as one object instead of two parameters
    that always move together.

    Blast radius is ONE claimed job: the scrape, the conversion, and the
    report POST all route through the guard (dag.guard) — a failure is
    reported to the queue, or logged when even that POST fails. The worker
    never raises out of here and never retries: the queue's backoff decides
    when the job becomes claimable again.
    """

    def __init__(self, client: httpx.AsyncClient, job: ScrapeJob) -> None:
        self.client = client
        self.job = job

    async def run(self) -> None:
        """Scrape the job and report the outcome; never raises."""
        await guarded_async(self._scrape_and_report, self._report_failure)

    async def _scrape_and_report(self) -> None:
        """Scrape the job, convert it, and POST the success report."""
        prop = await scrape(self.job.url)
        if prop is None:
            raise RuntimeError("scrape returned no data (offline mode / login wall?)")
        if not prop.address:
            raise RuntimeError("no address parsed (Rightmove block/login page?)")
        listing = ScrapedListing(
            url=prop.url or self.job.url,
            address=prop.address,
            postcode=prop.postcode,
            bedrooms=prop.bedrooms,
            price=prop.price,
            latitude=prop.latitude,
            longitude=prop.longitude,
        )
        resp = await self.client.post(
            f"{_app_url()}/api/scrapes/report",
            headers=_headers(),
            json={"job_id": self.job.id, "ok": True, "data": listing.__dict__},
        )
        resp.raise_for_status()
        print(f"scraped {self.job.rid} ok: {listing.address}")

    async def _report_failure(self, exc: Exception) -> None:
        """Report the job's failure; if even that POST fails, log both and give up."""
        _, report_failure = await capture_async(lambda: self._post_failure_report(exc))
        if report_failure is not None:
            print(f"FAILED to report job {self.job.id}: {report_failure}")
        print(f"scrape failed for {self.job.rid}: {exc}")

    async def _post_failure_report(self, exc: Exception) -> None:
        """POST the failure report for one job; a non-2xx status raises."""
        resp = await self.client.post(
            f"{_app_url()}/api/scrapes/report",
            headers=_headers(),
            json={"job_id": self.job.id, "ok": False, "error": str(exc)[:500]},
        )
        resp.raise_for_status()


async def run_once() -> int:
    url = _app_url()
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(f"{url}/api/scrapes/claim", headers=_headers())
        resp.raise_for_status()
        raw = resp.json().get("job")
        if raw is None:
            return 0
        job = ScrapeJob(id=raw["id"], rid=raw["rid"], url=raw["url"])
        await _ClaimedJob(client, job).run()
        return 1


async def run_loop(interval: float) -> None:
    while True:
        _, failure = await capture_async(run_once)
        if failure is not None:
            print(f"worker error: {failure}")
        # Always sleep — a transient error must not tight-loop the poller.
        await asyncio.sleep(interval)


def main() -> None:
    # systemd pipes stdout — Python block-buffers pipes, so prints would
    # never reach journald until the buffer fills or the process exits.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]  # reconfigure exists at runtime (3.7+); the TextIO stub omits it
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="process one job (or none) and exit")
    parser.add_argument("--loop", action="store_true", help="poll forever (default)")
    parser.add_argument("--interval", type=float, default=60.0, help="poll interval seconds (loop mode)")
    args = parser.parse_args()
    if args.once:
        raise SystemExit(asyncio.run(run_once()))
    asyncio.run(run_loop(args.interval))


if __name__ == "__main__":
    main()
