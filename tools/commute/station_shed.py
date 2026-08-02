"""Station shed — one-off TfL batch producing the commutable-station set.

Phase 1 of the Rightmove commute search toolchain (docs/rightmove-commute-monitor.md):

- Route every station inside a bounding box (by lat/lon, TfL coordinate origins) to
  the Pimlico and Aldgate office postcodes.
- Stations within the inner zone (kept without routing) or with
  ``min(duration) <= threshold`` are kept.
- Output is committed to ``data/commute/station_shed.json`` so the rest of the
  toolchain runs offline.

TfL is a free public API with no hard quota; this is a one-off, sequential,
disk-cached batch (~2960 calls, ~50 min at ~1 req/s). Transient errors (429/5xx)
retry with backoff; other failures exclude the station (an accepted false
negative — see the plan's known-limitations section).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from houses.geo import GeoPoint

logger = logging.getLogger(__name__)

DEFAULT_BBOX = {"lat_min": 50.1, "lat_max": 53.6, "lon_min": -4.0, "lon_max": 2.0}
INNER_RADIUS_KM = 20.0
THRESHOLD_MIN = 132
DEFAULT_CSV = Path("data/stations.csv")
DEFAULT_OUT = Path("data/commute/station_shed.json")
ENGINE_VERSION = "station-shed-v1"

_POSTCODE_RE = re.compile(r"[A-Z]{1,2}[0-9][A-Z0-9]?(?:\s*[0-9][A-Z]{2})?")


@dataclass(frozen=True)
class BBox:
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def contains(self, lat: float, lon: float) -> bool:
        return self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max


@dataclass(frozen=True)
class Station:
    name: str
    crs: str
    lat: float
    lon: float

    @property
    def point(self) -> GeoPoint:
        return GeoPoint(self.lat, self.lon)

    def distance_km_to(self, other: GeoPoint) -> float:
        return self.point.distance_km_to(other)


@dataclass(frozen=True)
class Office:
    postcode: str
    point: GeoPoint


def load_stations(csv_path: str | Path) -> list[Station]:
    """Load stations from the repo's stations.csv (name, lat, long, crsCode)."""
    stations: list[Station] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            stations.append(
                Station(
                    name=row["stationName"],
                    crs=row["crsCode"],
                    lat=float(row["lat"]),
                    lon=float(row["long"]),
                )
            )
    return stations


def keep_station(inner: bool, dur_p: int | None, dur_a: int | None, threshold: int) -> bool:
    """Keep rule: inner-zone stations are definitionally in; otherwise min duration wins.

    ``None`` durations mean the route failed — they never count as in, and a
    station with no successful route to either office is dropped.
    """
    if inner:
        return True
    durations = [d for d in (dur_p, dur_a) if d is not None]
    return bool(durations) and min(durations) <= threshold


async def build_shed(
    stations: list[Station],
    offices: list[Office],
    bbox: BBox,
    inner_radius_km: float,
    threshold: int,
    router,
    *,
    delay_s: float = 0.5,
    existing_records: list[dict] | None = None,
    checkpoint: Callable[[list[dict], int], None] | None = None,
) -> list[dict]:
    """Route stations in the box and return shed records.

    ``router`` is ``async (Station, postcode) -> int | None`` (duration minutes,
    or None when no route/failure). Stations outside the box are skipped; inner-zone
    stations are kept without a single router call.

    Resumable: ``existing_records`` (a previous run's output) marks stations as
    done — they are never re-routed, never re-delayed, and the final record list
    is byte-identical to a from-scratch run. ``checkpoint(records, processed)``
    is invoked after each newly processed station so the caller can persist
    progress incrementally (a killed batch loses at most the in-flight station).
    """
    done = {r["crs"] for r in existing_records} if existing_records else set()
    records = list(existing_records) if existing_records else []
    order = {st.crs: i for i, st in enumerate(stations)}
    processed = 0
    for st in stations:
        if st.crs in done:
            continue
        if not bbox.contains(st.lat, st.lon):
            continue
        inner = any(st.distance_km_to(office.point) <= inner_radius_km for office in offices)
        if inner:
            records.append(_record(st, None, None, True))
        else:
            dur_p = await router(st, offices[0].postcode)
            dur_a = await router(st, offices[1].postcode)
            records.append(_record(st, dur_p, dur_a, keep_station(False, dur_p, dur_a, threshold)))
            if delay_s:
                await asyncio.sleep(delay_s)
        processed += 1
        if checkpoint is not None:
            checkpoint(records, processed)
    # Stable order regardless of resume point: input station order.
    records.sort(key=lambda r: order.get(r["crs"], len(order)))
    return records


def _record(st: Station, dur_p: int | None, dur_a: int | None, kept: bool) -> dict:
    return {
        "name": st.name,
        "crs": st.crs,
        "lat": st.lat,
        "lon": st.lon,
        "duration_pimlico": dur_p,
        "duration_aldgate": dur_a,
        "kept": kept,
    }


# ── TfL routing adapter ──────────────────────────────────────────────


async def route_station_duration(station: Station, dest_postcode: str, *, allow_bus: bool = True) -> int | None:
    """Route a station (lat/lon origin) to a destination postcode; return minutes or None.

    Reuses the app's TfL plumbing: the same URL shape, auth/date params, disk cache,
    and journey parsing as ``houses/tfl_client.py`` — with retry-with-backoff for
    transient errors (429/5xx). A retry marker is added to the cache key so a
    transient error body cached by the first attempt does not short-circuit the retry.
    """
    from houses.tfl_client import TflClient

    modes = ["tube", "overground", "dlr", "tram", "national-rail", "walking"]
    if allow_bus:
        modes.append("bus")

    url = f"{TflClient.TFL_JOURNEY_URL}/{station.lat},{station.lon}/to/{dest_postcode}"
    base_params = {
        "nationalSearch": "true",
        "timeIs": "arriving",
        "journeyPreference": "leasttime",
        "mode": ",".join(modes),
        **TflClient._next_weekday_date_params(),
        **TflClient._tfl_auth_params(),
    }

    data = await _cached_with_retry(url, base_params)
    if data is None:
        return None
    duration, _, _ = TflClient._pick_best_journey(data)
    return duration


async def _cached_with_retry(url: str, params: dict, *, attempts: int = 3, base_delay: float = 1.0) -> dict | None:
    """Cached TfL call with backoff on transient errors; None on persistent failure."""
    from dag.http_error import HttpError
    from houses.tfl_client import TflClient

    for attempt in range(attempts):
        retry_params = {**params, "_retry": str(attempt)} if attempt else params
        try:
            return await TflClient._cached_api_call(url, retry_params)
        except HttpError as e:
            if e.is_rate_limit() or e.is_server_error():
                delay = base_delay * (2**attempt)
                logger.warning("TfL transient %s for %s — retry in %.1fs", e.status, url, delay)
                await asyncio.sleep(delay)
                continue
            logger.warning("TfL client error %s for %s — station excluded", e.status, url)
            return None
    logger.error("TfL transient errors exhausted for %s — station excluded", url)
    return None


# ── CLI ──────────────────────────────────────────────────────────────


def _extract_postcode(address: str) -> str:
    match = _POSTCODE_RE.search(address.upper())
    if not match:
        raise ValueError(f"no postcode in {address!r}")
    return match.group(0).strip()


async def _geocode_offices() -> list[Office]:
    from houses.config import settings
    from houses.location import geocode

    offices: list[Office] = []
    for dest in (settings.simon_destination, settings.lorena_destination):
        pc = _extract_postcode(dest)
        point = (await geocode(pc)).value_or_none()
        if point is None:
            raise RuntimeError(f"could not geocode office postcode {pc!r}")
        offices.append(Office(pc, point))
    return offices


def is_complete(existing: list[dict] | None, records: list[dict], expected: int) -> bool:
    """True when a resume found every expected station already done.

    A killed batch (records < expected) must resume; a run that processed new
    stations (records > existing) must report its work, not "already complete".
    """
    return existing is not None and len(records) >= expected and len(records) == len(existing)


def _write_payload(path: Path, metadata: dict, records: list[dict]) -> None:
    """Write the shed payload atomically — a kill mid-write never corrupts the file."""
    payload = {"metadata": metadata, "stations": records}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, path)


async def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the commutable-station shed (one-off TfL batch).")
    parser.add_argument("--csv", default=str(DEFAULT_CSV))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--limit", type=int, default=0, help="route at most N stations (smoke tests)")
    parser.add_argument("--force", action="store_true", help="wipe an existing shed and re-run everything")
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between stations (default 0.5)")
    parser.add_argument("--checkpoint-every", type=int, default=25, help="rewrite the output file every N stations")
    args = parser.parse_args(argv)

    out_path = Path(args.out)
    metadata = None
    existing: list[dict] | None = None
    if out_path.exists() and not args.force:
        try:
            prev = json.loads(out_path.read_text())
            existing = prev["stations"]
            metadata = prev["metadata"]  # keep the original generated_at across resumes
            print(f"resuming from {out_path} ({len(existing or [])} stations already done)")
        except (json.JSONDecodeError, KeyError, OSError):
            print(f"{out_path} unreadable — starting fresh", file=sys.stderr)
            existing = None
    elif out_path.exists() and args.force:
        print("--force: wiping the existing shed", file=sys.stderr)

    offices = await _geocode_offices()
    stations = load_stations(args.csv)
    if args.limit:
        stations = stations[: args.limit]

    bbox = BBox(**DEFAULT_BBOX)
    expected = sum(1 for st in stations if bbox.contains(st.lat, st.lon))
    if metadata is None:
        metadata = {
            "threshold_min": THRESHOLD_MIN,
            "destinations": [o.postcode for o in offices],
            "bbox": DEFAULT_BBOX,
            "inner_radius_km": INNER_RADIUS_KM,
            "engine_version": ENGINE_VERSION,
            "expected_stations": expected,
            "generated_at": datetime.now(UTC).isoformat(),
        }
    else:
        metadata = {**metadata, "expected_stations": expected}

    def _checkpoint(records: list[dict], processed: int) -> None:
        if processed % args.checkpoint_every == 0:
            _write_payload(out_path, metadata, records)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    prev_count = len(existing) if existing is not None else 0
    started = time.monotonic()
    records = await build_shed(
        stations,
        offices,
        bbox,
        INNER_RADIUS_KM,
        THRESHOLD_MIN,
        route_station_duration,
        delay_s=args.delay,
        existing_records=existing,
        checkpoint=_checkpoint,
    )
    elapsed = time.monotonic() - started

    if is_complete(existing, records, metadata.get("expected_stations", len(records))):
        print(f"shed already complete ({len(records)} stations) — use --force to re-run")
        return 0

    _write_payload(out_path, metadata, records)
    kept = sum(1 for r in records if r["kept"])
    new = len(records) - prev_count
    print(f"{new} new station(s) processed ({kept} kept of {len(records)}) in {elapsed:.0f}s → {out_path}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(asyncio.run(run()))
