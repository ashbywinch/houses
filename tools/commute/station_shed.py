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

from pydantic import ValidationError

from houses.geopoint import GeoPoint
from houses.location import geocode
from houses.settings import settings
from houses.tfl_client import TflClient
from serialization import SerializableAsDict

logger = logging.getLogger(__name__)


class StationShedConfig:
    """Constants of the station shed batch (review-log: one config class owns
    the bbox, thresholds, and version identity)."""

    DEFAULT_BBOX = {"lat_min": 50.1, "lat_max": 53.6, "lon_min": -4.0, "lon_max": 2.0}
    INNER_RADIUS_KM = 20.0
    THRESHOLD_MIN = 132
    ENGINE_VERSION = "station-shed-v1"
    MINUTES_PER_HOUR = 60.0


DEFAULT_CSV = Path("data/stations.csv")
DEFAULT_OUT = Path("data/commute/station_shed.json")

_POSTCODE_RE = re.compile(r"[A-Z]{1,2}[0-9][A-Z0-9]?(?:\s*[0-9][A-Z]{2})?")

# ── the artifact's records ───────────────────────────────────────────


type _ResumeRecords = tuple[set[str], list[ShedRecord]]


@dataclass(frozen=True)
class BBox:
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def contains(self, lat: float, lon: float) -> bool:
        return self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max


@dataclass(frozen=True)
class ImplausibleCheck:
    """Both route durations after the physical-plausibility screen."""

    dur_p: int | None
    dur_a: int | None


@dataclass(frozen=True)
class Station:
    name: str
    crs: str
    lat: float
    lon: float

    @property
    def point(self) -> GeoPoint:
        return GeoPoint(self.lat, self.lon)


    # ── Routing adapter ──────────────────────────────────────────────

    def origin_candidates(self) -> list[str]:
        """Origin identifiers to try in order — coordinate origin first, then name.

        TfL's JourneyResults 404s on lat/lon origins for many stations (its geo
        stop-finder fails outside London — observed for 1075 of 1819 in the shed
        batch); routing from ``'<name> Rail Station'`` resolves them. The name form
        can itself 300-disambiguate (e.g. "Peterborough" matches streets), which
        counts as a failure — the coords form already failed by then.
        """
        candidates = [f"{self.lat},{self.lon}"]
        name = self.name
        if not name.lower().endswith(" rail station"):
            name += " Rail Station"
        candidates.append(name)
        return candidates

    async def route_duration(
        self,
        dest_postcode: str,
        *,
        allow_bus: bool = True,
        fetch: Callable[[str, str], Awaitable[int | None]] | None = None,
    ) -> int | None:
        """Route this station to a destination postcode; return minutes or None.

        Tries each :meth:`origin_candidates` origin in order — coordinate origin
        first, then ``'<name> Rail Station'`` — and returns the first routed
        duration. Routing itself is ``TflClient.route_duration`` (public API:
        same request shape as the app's planner, disk-cached, retry-with-backoff
        on transient errors). ``fetch`` is injectable for tests (default: the
        TfL client).
        """
        if fetch is None:

            async def _default_fetch(origin: str, dest: str) -> int | None:
                return await TflClient.route_duration(origin, dest, allow_bus=allow_bus)

            fetch = _default_fetch
        for origin in self.origin_candidates():
            duration = await fetch(origin, dest_postcode)
            if duration is not None:
                return duration
        return None

    def reject_implausible(
        self, offices: list[Office], dur_p: int | None, dur_a: int | None
    ) -> ImplausibleCheck:
        """Null out durations that are physically impossible for the distance.

        TfL's name-origin fallback can resolve to the wrong place (observed:
        Worcestershire Parkway, ~160 km out, reported 35 min — ~274 km/h). A
        door-to-door average above ~150 km/h is not achievable on the UK network,
        so a faster duration means the origin resolved wrongly — treat it as a
        failed route rather than silently extending coverage to the wrong area.
        """
        speed_cap_kmh = 150.0
        floors = [
            self.point.distance_km_to(office.point) / speed_cap_kmh * StationShedConfig.MINUTES_PER_HOUR
            for office in offices
        ]
        if dur_p is not None and dur_p < floors[0]:
            dur_p = None
        if dur_a is not None and dur_a < floors[1]:
            dur_a = None
        return ImplausibleCheck(dur_p=dur_p, dur_a=dur_a)

    def record(
        self, dur_p: int | None, dur_a: int | None, kept: bool, routing_error: str | None = None
    ) -> ShedRecord:
        """The wire-format shed record for this station."""
        return ShedRecord(
            name=self.name, crs=self.crs, lat=self.lat, lon=self.lon,
            duration_pimlico=dur_p, duration_aldgate=dur_a, kept=kept,
            routing_error=routing_error,
        )

    async def process(self, ctx: RoutingContext) -> ShedRecord | None:
        """Route this station → its shed record, or None when it is outside the box.

        Inner-zone stations are kept without a single router call. Both
        destinations are routed concurrently — the two calls share no state and
        TfL latency dominates; stations remain strictly sequential.
        """
        if not ctx.bbox.contains(self.lat, self.lon):
            return None
        inner = any(self.point.distance_km_to(office.point) <= ctx.inner_radius_km for office in ctx.offices)
        if inner:
            return self.record(None, None, kept=True)
        dur_p, dur_a = await asyncio.gather(
            ctx.router(self, ctx.offices[0].postcode),
            ctx.router(self, ctx.offices[1].postcode),
        )
        checked = self.reject_implausible(ctx.offices, dur_p, dur_a)
        routed = checked.dur_p is not None or checked.dur_a is not None
        rec = self.record(
            checked.dur_p,
            checked.dur_a,
            keep_station(inner=False, dur_p=checked.dur_p, dur_a=checked.dur_a, threshold=ctx.threshold),
            routing_error=None if routed else "failed",
        )
        if ctx.delay_s:
            await asyncio.sleep(ctx.delay_s)
        return rec


@dataclass(frozen=True)
class Office:
    postcode: str
    point: GeoPoint


class ShedRecord(SerializableAsDict):
    """One station's row in station_shed.json."""

    name: str
    crs: str
    lat: float
    lon: float
    duration_pimlico: int | None
    duration_aldgate: int | None
    kept: bool
    routing_error: str | None = None

class ShedMetadata(SerializableAsDict):
    """Current-constants metadata for the shed payload."""

    threshold_min: int
    destinations: list[str]
    bbox: dict[str, float]
    inner_radius_km: float
    engine_version: str
    expected_stations: int
    generated_at: str

class ShedConfigSignature(SerializableAsDict):
    """The config identity a shed was built under."""

    engine_version: str
    destinations: list[str]
    threshold_min: int
    bbox: dict[str, float]
    inner_radius_km: float


class ShedPayload(SerializableAsDict):
    """station_shed.json — the artifact the map reads.

    The payload owns its bytes: ``to_json()`` is the one place the artifact
    becomes text, so the key order and the indentation live here.
    """

    metadata: ShedMetadata
    stations: list[ShedRecord]

    def to_json(self) -> str:
        return json.dumps(self.model_dump(), indent=2)

def load_stations(csv_path: str | Path) -> list[Station]:
    """Load stations from the repo's stations.csv (name, lat, long, crsCode)."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        return [
            Station(
                name=row["stationName"],
                crs=row["crsCode"],
                lat=float(row["lat"]),
                lon=float(row["long"]),
            )
            for row in csv.DictReader(f)
        ]


def keep_station(inner: bool, dur_p: int | None, dur_a: int | None, threshold: int) -> bool:
    """Keep rule: inner-zone stations are definitionally in; otherwise min duration wins.

    ``None`` durations mean the route failed — they never count as in, and a
    station with no successful route to either office is dropped.
    """
    if inner:
        return True
    durations = [d for d in (dur_p, dur_a) if d is not None]
    return bool(durations) and min(durations) <= threshold


def _resume_records(
    existing_records: list[ShedRecord] | None, stations: list[Station]
) -> _ResumeRecords:
    """Records a resume carries forward, and the CRSes marked done.

    Only completed records are done. A record with a routing_error (both
    destinations failed) is NOT done: a resume re-routes it, so a transient
    TfL outage that outlasted the retry window gets another chance instead of
    permanently excluding the station. Records whose CRS is absent from the
    current station list (station removed) or whose coords differ (station
    moved) are pruned as stale — a resume can never silently keep outdated
    records.
    """
    by_crs = {st.crs: st for st in stations}
    done: set[str] = set()
    records: list[ShedRecord] = []
    for rec in existing_records or []:
        st = by_crs.get(rec.crs)
        if st is not None and st.lat == rec.lat and st.lon == rec.lon:
            if not rec.routing_error:
                done.add(rec.crs)
            records.append(rec)
    return done, records


@dataclass(frozen=True)
class RoutingContext:
    """The batch's routing policy — shared by every station in build_shed."""

    offices: list[Office]
    bbox: BBox
    inner_radius_km: float
    threshold: int
    router: Callable[[Station, str], Awaitable[int | None]]
    delay_s: float


async def build_shed(
    stations: list[Station],
    ctx: RoutingContext,
    *,
    existing_records: list[ShedRecord] | None = None,
    checkpoint: Callable[[list[ShedRecord], int], None] | None = None,
) -> list[ShedRecord]:
    """Route stations in the box and return shed records.

    ``ctx.router`` is ``async (Station, postcode) -> int | None`` (duration
    minutes, or None when no route/failure) — pass ``Station.route_duration``
    for real TfL routing. Stations outside the box are skipped; inner-zone
    stations are kept without a single router call.

    Resumable: ``existing_records`` (a previous run's output) marks stations as
    done — they are never re-routed, never re-delayed, and the final record list
    is byte-identical to a from-scratch run (see ``_resume_records``). Note the
    guarantee: a killed batch loses at most ``checkpoint_every - 1``
    already-processed stations of progress (the caller's write cadence); those
    are re-processed on resume, but every TfL response is disk-cached, so
    re-processing COMPLETED stations is cache hits. Failed records are re-routed
    with fresh calls — error responses are never cached, by design (a transient
    outage must get a genuine retry, not a poisoned cache replay).
    ``checkpoint(records, processed)`` is invoked after each newly processed
    station so the caller can persist progress incrementally.
    """
    done, records = _resume_records(existing_records, stations)
    processed = 0
    order = {st.crs: i for i, st in enumerate(stations)}
    for st in stations:
        if st.crs in done:
            continue
        # Re-processing a station (routing_error record from a previous run, or
        # moved coords) must REPLACE its old record, not duplicate it.
        records = [r for r in records if r.crs != st.crs]
        rec = await st.process(ctx)
        if rec is None:
            continue
        records.append(rec)
        processed += 1
        if checkpoint is not None:
            checkpoint(records, processed)
    # Stable order regardless of resume point: input station order.
    records.sort(key=lambda r: order.get(r.crs, len(order)))
    return records



# ── CLI ──────────────────────────────────────────────────────────────


def _extract_postcode(address: str) -> str:
    match = _POSTCODE_RE.search(address.upper())
    if not match:
        raise ValueError(f"no postcode in {address!r}")
    return match.group(0).strip()


async def _geocode_offices() -> list[Office]:

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


def build_metadata(offices: list[Office], expected: int, generated_at: str) -> ShedMetadata:
    """Current-constants metadata for the shed payload.

    ``generated_at`` is the batch start time — preserved across resumes so a
    killed-and-resumed batch keeps its original identity. Everything else is
    rebuilt from current constants so a resume can never silently keep
    outdated destinations/bbox/threshold values.
    """
    return ShedMetadata(
        threshold_min=StationShedConfig.THRESHOLD_MIN,
        destinations=[o.postcode for o in offices],
        bbox=StationShedConfig.DEFAULT_BBOX,
        inner_radius_km=StationShedConfig.INNER_RADIUS_KM,
        engine_version=StationShedConfig.ENGINE_VERSION,
        expected_stations=expected,
        generated_at=generated_at,
    )


def is_complete(existing: list[ShedRecord] | None, records: list[ShedRecord], expected: int) -> bool:
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
        and not any(r.routing_error for r in records)
    )


def config_signature(offices: list[Office]) -> ShedConfigSignature:
    """The config identity a shed was built under — engine, destinations,
    threshold, bbox, inner zone. If any of these change, previously-routed
    records (routed to the OLD destinations/params) must not be resumed: they
    would mix with new metadata claiming the new config.
    """
    return ShedConfigSignature(
        engine_version=StationShedConfig.ENGINE_VERSION,
        destinations=[o.postcode for o in offices],
        threshold_min=StationShedConfig.THRESHOLD_MIN,
        bbox=StationShedConfig.DEFAULT_BBOX,
        inner_radius_km=StationShedConfig.INNER_RADIUS_KM,
    )


def resume_allowed(prev_metadata: ShedMetadata, current: ShedConfigSignature) -> bool:
    """A resume is only safe when the shed was built under the CURRENT config.

    Comparing only the engine version lets a config change (destinations,
    threshold, bbox) slip through without a version bump — the resume would
    keep records routed to the old config while the metadata claims the new
    one. Compare the full config identity.
    """
    return all(getattr(prev_metadata, k) == v for k, v in current.model_dump().items())


@dataclass(frozen=True)
class ResumeState:
    """The run's starting point: resumed records and metadata, or an abort exit."""

    existing: list[ShedRecord] | None
    metadata: ShedMetadata | None
    abort: int | None


@dataclass(frozen=True)
class LimitedStations:
    """The station list after --limit, or the exit code refusing a truncating run."""

    stations: list[Station]
    exit_code: int | None


class _ShedFile:
    """The shed artifact on disk — resume state, atomic writes, and the guards
    that keep a resume or a ``--limit`` run from silently corrupting it.

    Owns the output path and the offices its config signature is checked
    against, so the file's operations are methods on the state they read
    instead of module functions threading ``out_path`` through every hop.
    """

    def __init__(self, path: Path, offices: list[Office]) -> None:
        self.path: Path = path
        self._offices: list[Office] = offices

    def resume_or_wipe(self, force: bool) -> ResumeState:
        """The run's starting point: resumed records + metadata, or a fresh batch.

        ``abort`` is an exit code when the run must stop before routing (e.g. a
        corrupt existing shed).
        """
        if self.path.exists() and not force:
            resumed = self._resume_state()
            if resumed.abort is not None:
                return ResumeState(None, None, resumed.abort)
            if resumed.existing is not None:
                print(f"resuming from {self.path} ({len(resumed.existing)} stations already done)")
            return resumed
        if self.path.exists() and force:
            logger.warning("--force: wiping the existing shed at %s", self.path)
            print("--force: wiping the existing shed", file=sys.stderr)
        return ResumeState(None, None, None)

    def _resume_state(self) -> ResumeState:
        """Load the existing shed for resuming.

        Returns a resumable state, an abort exit (config mismatch), or a fresh
        start (unreadable shed).
        """
        signature = config_signature(self._offices)
        try:
            prev = ShedPayload(**json.loads(self.path.read_text()))
            if not resume_allowed(prev.metadata, signature):
                logger.warning(
                    "shed config mismatch: stored metadata %s != current %s — refusing to resume %s",
                    {k: getattr(prev.metadata, k) for k in signature.model_dump()},
                    signature.model_dump(),
                    self.path,
                )
                print(
                    f"{self.path} was built under a different config (destinations/threshold/bbox/version) — "
                    "use --force to rebuild",
                    file=sys.stderr,
                )
                return ResumeState(None, None, 1)
            return ResumeState(prev.stations, prev.metadata, None)
        except (json.JSONDecodeError, ValidationError, KeyError, OSError):
            logger.warning("unreadable shed at %s — starting fresh (corrupt or truncated write?)", self.path)
            print(f"{self.path} unreadable — starting fresh", file=sys.stderr)
            return ResumeState(None, None, None)

    def write(self, metadata: ShedMetadata, records: list[ShedRecord]) -> None:
        """Write the shed payload atomically — a kill mid-write never corrupts the file."""
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(ShedPayload(metadata=metadata, stations=records).to_json())
        os.replace(tmp, self.path)

    def limit_stations(self, stations: list[Station], limit: int) -> LimitedStations:
        """Apply --limit, refusing a run that would truncate an existing shed.

        A --limit run would write only the first N stations, silently truncating
        the existing shed — --force means "re-run everything", never "keep only N".
        Smoke runs must use a temp --out.
        """
        if limit and self.path.exists():
            logger.warning(
                "refusing --limit run: %s already exists and would be truncated to %d stations", self.path, limit
            )
            print(
                f"refusing --limit run: {self.path} already exists and would be truncated — "
                "use --out with a temp path for smoke tests",
                file=sys.stderr,
            )
            return LimitedStations(stations, 1)
        if limit:
            stations = stations[:limit]
        return LimitedStations(stations, None)


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
    shed_file = _ShedFile(out_path, offices)
    resume = shed_file.resume_or_wipe(args.force)
    if resume.abort is not None:
        return resume.abort

    stations = load_stations(args.csv)
    limited = shed_file.limit_stations(stations, args.limit)
    if limited.exit_code is not None:
        return limited.exit_code
    stations = limited.stations

    bbox = BBox(**StationShedConfig.DEFAULT_BBOX)
    expected = sum(1 for st in stations if bbox.contains(st.lat, st.lon))
    # Always rebuild from current constants; preserve only the batch-start
    # timestamp across resumes (a resume must never keep outdated settings).
    generated_at = (
        resume.metadata.generated_at if resume.metadata else datetime.now(UTC).isoformat()
    ) or datetime.now(UTC).isoformat()
    metadata = build_metadata(offices, expected, generated_at)

    def _checkpoint(records: list[ShedRecord], processed: int) -> None:
        if processed % args.checkpoint_every == 0:
            shed_file.write(metadata, records)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    prev_count = len(resume.existing) if resume.existing is not None else 0
    started = time.monotonic()
    records = await build_shed(
        stations,
        RoutingContext(
            offices=offices,
            bbox=bbox,
            inner_radius_km=StationShedConfig.INNER_RADIUS_KM,
            threshold=StationShedConfig.THRESHOLD_MIN,
            router=Station.route_duration,
            delay_s=args.delay,
        ),
        existing_records=resume.existing,
        checkpoint=_checkpoint,
    )
    elapsed = time.monotonic() - started

    if is_complete(resume.existing, records, metadata.expected_stations):
        logger.warning(
            "shed already complete (%d stations) — no stations to process; use --force to re-run", len(records)
        )
        print(f"shed already complete ({len(records)} stations) — use --force to re-run")
        return 0

    shed_file.write(metadata, records)
    kept = sum(1 for r in records if r.kept)
    new = len(records) - prev_count
    print(f"{new} new station(s) processed ({kept} kept of {len(records)}) in {elapsed:.0f}s → {out_path}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(asyncio.run(run()))
