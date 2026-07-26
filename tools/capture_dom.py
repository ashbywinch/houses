#!/usr/bin/env python3
"""Capture rendered DOM from the Vue frontend for comparison / debugging.

Usage:
    python tools/capture_dom.py [--output /tmp] [--list-only] [--detail-only]

Requires the backend (port 8080) and frontend (port 5173) to be running.

The script waits for both servers to respond before navigating.
A single browser instance is reused across captures.
"""

import asyncio
import sys
import time
from argparse import ArgumentParser
from datetime import UTC, datetime
from pathlib import Path

import httpx

FRONTEND_URL = "http://localhost:5173"
BACKEND_URL = "http://localhost:8080"
LAUNCH_OPTS = {"headless": True, "args": ["--no-sandbox", "--disable-gpu"]}


def _session_dir() -> Path:
    """A session-scoped output directory under the project's tools/ dir.

    Files are grouped by session date so they're attributable and don't
    accumulate in /tmp forever.
    """
    base = Path(__file__).resolve().parent / "captures"
    stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")
    session = base / stamp
    session.mkdir(parents=True, exist_ok=True)
    return session


_browser = None
_playwright = None


async def wait_for_server(url: str, label: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = await httpx.AsyncClient(timeout=3).get(url)
            if r.status_code < 500:
                return
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        await asyncio.sleep(1)
    sys.exit(f"{label} at {url} not ready after {timeout}s")


async def get_browser():
    global _browser, _playwright
    if _browser and _browser.is_connected():
        return _browser
    from playwright.async_api import async_playwright

    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(**LAUNCH_OPTS)
    return _browser


async def capture_page(url: str, output_dir: str, label: str):
    browser = await get_browser()
    context = await browser.new_context(
        viewport={"width": 1280, "height": 2048},
        ignore_https_errors=True,
    )
    page = await context.new_page()

    errors = []

    def on_console(msg):
        if msg.type == "error":
            errors.append(msg.text[:300])

    page.on("console", on_console)
    page.on("pageerror", lambda e: errors.append(f"PAGE: {str(e)[:300]}"))

    await page.goto(url, wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(5000)

    html = await page.content()
    html_path = Path(output_dir) / f"dom_{label}.html"
    html_path.write_text(html)
    print(f"  DOM saved ({len(html)} bytes) → {html_path}")

    screenshot_path = Path(output_dir) / f"screenshot_{label}.png"
    await page.screenshot(path=str(screenshot_path), full_page=True)
    print(f"  Screenshot → {screenshot_path}")

    if errors:
        print(f"  Console errors ({len(errors)}):")
        for e in errors[:5]:
            print(f"    {e}")

    cards = await page.query_selector_all(".card")
    print(f"  Cards: {len(cards)}")
    for i, card in enumerate(cards[:2]):
        text = (await card.text_content()).strip()[:500]
        print(f"\n  --- Card {i} ---")
        print(f"  {text}")

    await context.close()


async def main():
    parser = ArgumentParser(description="Capture Vue frontend DOM")
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output directory (default: tools/captures/<session-timestamp>)",
    )
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--detail-only", action="store_true")
    args = parser.parse_args()

    out = Path(args.output) if args.output else _session_dir()
    out.mkdir(parents=True, exist_ok=True)

    print("Waiting for servers …")
    await wait_for_server(BACKEND_URL + "/api/properties", "backend")
    await wait_for_server(FRONTEND_URL, "frontend")

    urls = []
    if not args.detail_only:
        urls.append((FRONTEND_URL + "/", "list"))
    if not args.list_only:
        urls.append((FRONTEND_URL + "/#/property/89306649", "detail"))

    for url, label in urls:
        print(f"\nCapturing {label} page …")
        await capture_page(url, out, label)

    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()


if __name__ == "__main__":
    asyncio.run(main())
