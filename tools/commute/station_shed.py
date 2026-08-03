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
from collections.abc import Awaitable, Callable
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
    is byte-identical to a from-scratch run. Records whose CRS is absent from
    the current station list (station removed) or whose coords differ (station
    moved) are pruned as stale and, if present, re-processed — a resume can
    never silently keep outdated records. ``checkpoint(records, processed)``
    is invoked after each newly processed station so the caller can persist
    progress incrementally. Note the guarantee: a killed batch loses at most
    ``checkpoint_every - 1`` already-processed stations of progress (the caller's
    write cadence); those are re-processed on resume, but every TfL response is
    disk-cached, so re-processing COMPLETED stations is cache hits. Failed
    records are re-routed with fresh calls — error responses are never cached,
    by design (a transient outage must get a genuine retry, not a poisoned
    cache replay)."""
    by_crs = {st.crs: st for st in stations}
    done: set[str] = set()
    records: list[dict] = []
    for rec in existing_records or []:
        st = by_crs.get(rec["crs"])
        if st is not None and st.lat == rec["lat"] and st.lon == rec["lon"]:
            # Only completed records are done. A record with a routing_error
            # (both destinations failed) is NOT done: a resume re-routes it,
            # so a transient TfL outage that outlasted the retry window gets
            # another chance instead of permanently excluding the station.
            if not rec.get("routing_error"):
                done.add(rec["crs"])
            records.append(rec)
    order = {st.crs: i for i, st in enumerate(stations)}
    processed = 0
    for st in stations:
        if st.crs in done:
            continue
        # Re-processing a station (routing_error record from a previous run, or
        # moved coords) must REPLACE its old record, not duplicate it.
        records = [r for r in records if r["crs"] != st.crs]
        if not bbox.contains(st.lat, st.lon):
            continue
        inner = any(st.distance_km_to(office.point) <= inner_radius_km for office in offices)
        if inner:
            records.append(_record(st, None, None, True))
        else:
            # Both destinations concurrently — the two calls share no state and
            # TfL latency dominates; stations remain strictly sequential.
            dur_p, dur_a = await asyncio.gather(
                router(st, offices[0].postcode),
                router(st, offices[1].postcode),
            )
            dur_p, dur_a = _reject_implausible(st, offices, dur_p, dur_a)
            routed = dur_p is not None or dur_a is not None
            records.append(
                _record(
                    st,
                    dur_p,
                    dur_a,
                    keep_station(False, dur_p, dur_a, threshold),
                    routing_error=None if routed else "failed",
                )
            )
            if delay_s:
                await asyncio.sleep(delay_s)
        processed += 1
        if checkpoint is not None:
            checkpoint(records, processed)
    # Stable order regardless of resume point: input station order.
    records.sort(key=lambda r: order.get(r["crs"], len(order)))
    return records


def _reject_implausible(
    st: Station, offices: list[Office], dur_p: int | None, dur_a: int | None
) -> tuple[int | None, int | None]:
    """Null out durations that are physically impossible for the distance.

    TfL's name-origin fallback can resolve to the wrong place (observed:
    Worcestershire Parkway, ~160 km out, reported 35 min — ~274 km/h). A
    door-to-door average above ~150 km/h is not achievable on the UK network,
    so a faster duration means the origin resolved wrongly — treat it as a
    failed route rather than silently extending coverage to the wrong area.
    """
    speed_cap_kmh = 150.0
    floors = [st.distance_km_to(office.point) / speed_cap_kmh * 60.0 for office in offices]
    if dur_p is not None and dur_p < floors[0]:
        dur_p = None
    if dur_a is not None and dur_a < floors[1]:
        dur_a = None
    return dur_p, dur_a


def _record(st: Station, dur_p: int | None, dur_a: int | None, kept: bool, routing_error: str | None = None) -> dict:
    return {
        "name": st.name,
        "crs": st.crs,
        "lat": st.lat,
        "lon": st.lon,
        "duration_pimlico": dur_p,
        "duration_aldgate": dur_a,
        "kept": kept,
        "routing_error": routing_error,
    }


# ── TfL routing adapter ──────────────────────────────────────────────


def origin_candidates(station: Station) -> list[str]:
    """Origin identifiers to try in order — coordinate origin first, then name.

    TfL's JourneyResults 404s on lat/lon origins for many stations (its geo
    stop-finder fails outside London — observed for 1075 of 1819 in the shed
    batch); routing from ``'<name> Rail Station'`` resolves them. The name form
    can itself 300-disambiguate (e.g. "Peterborough" matches streets), which
    counts as a failure — the coords form already failed by then.
    """
    candidates = [f"{station.lat},{station.lon}"]
    name = station.name
    if not name.lower().endswith(" rail station"):
        name += " Rail Station"
    candidates.append(name)
    return candidates


async def route_station_duration(
    station: Station,
    dest_postcode: str,
    *,
    allow_bus: bool = True,
    fetch: Callable[[str, str], Awaitable[int | None]] | None = None,
) -> int | None:
    """Route a station to a destination postcode; return minutes or None.

    Tries each ``origin_candidates`` origin in order — coordinate origin first,
    then ``'<name> Rail Station'`` — and returns the first routed duration.
    Routing itself is ``TflClient.route_duration`` (public API: same request
    shape as the app's planner, disk-cached, retry-with-backoff on transient
    errors). ``fetch`` is injectable for tests (default: the TfL client).
    """
    from houses.tfl_client import TflClient

    if fetch is None:

        async def _default_fetch(origin: str, dest: str) -> int | None:
            return await TflClient.route_duration(origin, dest, allow_bus=allow_bus)

        fetch = _default_fetch
    for origin in origin_candidates(station):
        duration = await fetch(origin, dest_postcode)
        if duration is not None:
            return duration
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
            logger.error(
                "could not geocode office postcode %r (from %r) — check houses/config.py destinations", pc, dest
            )
            raise RuntimeError(
                f"could not geocode office postcode {pc!r} — check the destination settings in houses/config.py"
            )
        offices.append(Office(pc, point))
    return offices


def build_metadata(offices: list[Office], expected: int, generated_at: str) -> dict:
    """Current-constants metadata for the shed payload.

    ``generated_at`` is the batch start time — preserved across resumes so a
    killed-and-resumed batch keeps its original identity. Everything else is
    rebuilt from current constants so a resume can never silently keep
    outdated destinations/bbox/threshold values.
    """
    return {
        "threshold_min": THRESHOLD_MIN,
        "destinations": [o.postcode for o in offices],
        "bbox": DEFAULT_BBOX,
        "inner_radius_km": INNER_RADIUS_KM,
        "engine_version": ENGINE_VERSION,
        "expected_stations": expected,
        "generated_at": generated_at,
    }


def is_complete(existing: list[dict] | None, records: list[dict], expected: int) -> bool:
    """True when a resume found every expected station already done.

    A killed batch (records < expected) must resume; a run that processed new
    stations or REPLACED a stale record (records != existing, even at equal
    length) must report its work and write the result, not "already complete".
    A shed with any ``routing_error`` record is unfinished work — a resume must
    re-route those stations (a transient outage gets another chance).
    """
    return (
        existing is not None
        and len(records) >= expected
        and records == existing
        and not any(r.get("routing_error") for r in records)
    )


def config_signature(offices: list[Office]) -> dict:
    """The config identity a shed was built under — engine, destinations,
    threshold, bbox, inner zone. If any of these change, previously-routed
    records (routed to the OLD destinations/params) must not be resumed: they
    would mix with new metadata claiming the new config.
    """
    return {
        "engine_version": ENGINE_VERSION,
        "destinations": [o.postcode for o in offices],
        "threshold_min": THRESHOLD_MIN,
        "bbox": DEFAULT_BBOX,
        "inner_radius_km": INNER_RADIUS_KM,
    }


def resume_allowed(prev_metadata: dict, current: dict) -> bool:
    """A resume is only safe when the shed was built under the CURRENT config.

    Comparing only the engine version lets a config change (destinations,
    threshold, bbox) slip through without a version bump — the resume would
    keep records routed to the old config while the metadata claims the new
    one. Compare the full config identity.
    """
    return all(prev_metadata.get(k) == v for k, v in current.items())


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
    offices = await _geocode_offices()
    metadata = None
    existing: list[dict] | None = None
    if out_path.exists() and not args.force:
        try:
            prev = json.loads(out_path.read_text())
            if not resume_allowed(prev["metadata"], config_signature(offices)):
                logger.warning(
                    "shed config mismatch: stored metadata %s != current %s — refusing to resume %s",
                    {k: prev["metadata"].get(k) for k in config_signature(offices)},
                    config_signature(offices),
                    out_path,
                )
                print(
                    f"{out_path} was built under a different config (destinations/threshold/bbox/version) — "
                    "use --force to rebuild",
                    file=sys.stderr,
                )
                return 1
            existing = prev["stations"]
            metadata = prev["metadata"]  # keep the original generated_at across resumes
            print(f"resuming from {out_path} ({len(existing or [])} stations already done)")
        except (json.JSONDecodeError, KeyError, OSError):
            logger.warning("unreadable shed at %s — starting fresh (corrupt or truncated write?)", out_path)
            print(f"{out_path} unreadable — starting fresh", file=sys.stderr)
            existing = None
    elif out_path.exists() and args.force:
        logger.warning("--force: wiping the existing shed at %s", out_path)
        print("--force: wiping the existing shed", file=sys.stderr)

    stations = load_stations(args.csv)
    if args.limit and out_path.exists():
        # A --limit run would write only the first N stations, silently
        # truncating the existing shed — --force means "re-run everything",
        # never "keep only N". Smoke runs must use a temp --out.
        logger.warning(
            "refusing --limit run: %s already exists and would be truncated to %d stations", out_path, args.limit
        )
        print(
            f"refusing --limit run: {out_path} already exists and would be truncated — "
            "use --out with a temp path for smoke tests",
            file=sys.stderr,
        )
        return 1
    if args.limit:
        stations = stations[: args.limit]

    bbox = BBox(**DEFAULT_BBOX)
    expected = sum(1 for st in stations if bbox.contains(st.lat, st.lon))
    # Always rebuild from current constants; preserve only the batch-start
    # timestamp across resumes (a resume must never keep outdated settings).
    generated_at = metadata["generated_at"] if metadata else datetime.now(UTC).isoformat()
    metadata = build_metadata(offices, expected, generated_at)

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
        logger.warning(
            "shed already complete (%d stations) — no stations to process; use --force to re-run", len(records)
        )
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
