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
from dataclasses import dataclass

import httpx

from houses.rightmove_scraper import scrape

# lucidlint: ignore private-import shared-secret cookie mint — the only minting entry point;
# same pattern as tools/deploy/release.sh
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


async def _run_job(client: httpx.AsyncClient, job: ScrapeJob) -> None:
    """Scrape one claimed job and report the outcome to the queue."""
    try:
        prop = await scrape(job.url)
        if prop is None:
            raise RuntimeError("scrape returned no data (offline mode / login wall?)")
        if not prop.address:
            raise RuntimeError("no address parsed (Rightmove block/login page?)")
        listing = ScrapedListing(
            url=prop.url or job.url,
            address=prop.address,
            postcode=prop.postcode,
            bedrooms=prop.bedrooms,
            price=prop.price,
            latitude=prop.latitude,
            longitude=prop.longitude,
        )
        resp = await client.post(
            f"{_app_url()}/api/scrapes/report",
            headers=_headers(),
            json={"job_id": job.id, "ok": True, "data": listing.__dict__},
        )
        resp.raise_for_status()
        print(f"scraped {job.rid} ok: {listing.address}")
    except Exception as exc:
        try:
            resp = await client.post(
                f"{_app_url()}/api/scrapes/report",
                headers=_headers(),
                json={"job_id": job.id, "ok": False, "error": str(exc)[:500]},
            )
            resp.raise_for_status()
            print(f"scrape failed for {job.rid}: {exc}")
            return
        except Exception as report_exc:
            print(f"FAILED to report job {job.id}: {report_exc}")
            print(f"scrape failed for {job.rid}: {exc}")
            return


async def run_once() -> int:
    url = _app_url()
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(f"{url}/api/scrapes/claim", headers=_headers())
        resp.raise_for_status()
        raw = resp.json().get("job")
        if raw is None:
            return 0
        job = ScrapeJob(id=raw["id"], rid=raw["rid"], url=raw["url"])
        await _run_job(client, job)
        return 1


async def run_loop(interval: float) -> None:
    while True:
        try:
            await run_once()
        except Exception as exc:
            print(f"worker error: {exc}")
            continue
        finally:
            # Always sleep — a transient error must not tight-loop the poller.
            await asyncio.sleep(interval)


def main() -> None:
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
