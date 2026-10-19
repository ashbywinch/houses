"""One-time BODS NeTEx fare data extraction script.

Downloads Bus Open Data Service NeTEx fare data for London commuter-belt
operators and extracts the fare model (zone structure + stop-to-zone
mappings + zone-pair prices) for routes that serve train stations.

Output: data/bus_fares.json — loaded at runtime for bus fare lookups.
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, ClassVar

from dag.guard import capture

# lucidlint: ignore private-import scripts-internal shared BODS API config (same one-shot package, review-log)
from scripts.download_bus_fares import (
    _BodsFares,
    checkpoint_path,
    download_dataset,
    get_bods_datasets,
)
from scripts.parse_netex_fares import (
    STATIONS_CSV,
    NetworkFare,
    Station,
    StopCoord,
    dataset_description_matches,
    load_naptan_stops,
    load_stations,
    parse_netex_fares,
)
from serialization import SerializableAsDict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def _abort_if_unset(value: object, message: str) -> bool:
    """Log *message* and flag an abort when *value* is falsy (fail-fast guard)."""
    if not value:
        logger.error(message)
        return True
    return False

OUTPUT_PATH = Path("data/bus_fares.json")

type NaptanStops = Mapping[str, tuple[float, float]]
"""NaPTAN stop coordinates keyed by ATCO code.  A keyed lookup table, not a
record: the keys are the country's stop codes, and ``parse_netex_fares``
indexes it by the ATCO code on each stop."""

type BodsDataset = dict[str, Any]
"""One dataset listing from the BODS fares API: the API's own JSON payload,
from which this script reads ``id`` and ``description``."""


type ZoneFlags = dict[str, bool]
"""Zone name → whether the stop is served by a zone that carries fares."""


type ZonePairFares = dict[str, dict[str, float]]
"""Zone-pair key (``"zone_a:zone_b"``) → fare product type → price."""

type StopZoneCandidates = dict[str, ZoneFlags]
"""Stop name → zone → whether the zone carries fares: a keyed collection, not a record."""






@dataclass(frozen=True)
class OperatorRef:
    """One BODS operator: its NOC code, display name, and the sub-operator
    names its datasets carry.

    The NOC's datasets are split per sub-operator, so those names belong to
    the operator itself — a second table keyed by display name would restate
    every operator's key and let the two drift apart.
    """

    noc: str
    display_name: str
    sub_operators: tuple[str, ...]


@dataclass(frozen=True)
class FareExtractionOptions:
    """The harness context every operator extraction shares: the station
    list, the BODS API key, and the harvest policy constants."""

    # Harvest policy — ClassVar keeps these out of the dataclass fields.
    OPERATORS: ClassVar[list[OperatorRef]] = [
        OperatorRef(noc="SCSO", display_name="Stagecoach_South", sub_operators=("Stagecoach South",)),
        OperatorRef(noc="SCSO", display_name="Stagecoach_South_East", sub_operators=("Stagecoach South East",)),
        OperatorRef(noc="SCOX", display_name="Stagecoach_Oxfordshire", sub_operators=("Stagecoach Oxfordshire",)),
        OperatorRef(noc="SCEM", display_name="Stagecoach_East_Midlands", sub_operators=("Stagecoach East Midlands",)),
        OperatorRef(noc="READ", display_name="Reading_Buses", sub_operators=("Reading",)),
        OperatorRef(noc="METR", display_name="Metrobus", sub_operators=("Metrobus",)),
        OperatorRef(noc="ABSS", display_name="Abellio", sub_operators=("Abellio",)),
        OperatorRef(
            noc="GALD",
            display_name="Go_Ahead_London",
            sub_operators=("Go-Ahead London", "Fastrack"),
        ),
    ]
    COORD_ROUND_DIGITS: ClassVar[int] = 4

    stations: list[Station]
    api_key: str
    cached_only: bool = False
    naptan: NaptanStops | None = None


class OperatorFares(SerializableAsDict):
    """The extraction result for one operator: zones, fares, stop coords.

    Its fields ARE the per-operator shape the bus-fares JSON stores; the writer
    projects the record with ``to_dict()``, which walks the nested stop records.
    """

    stop_zones: dict[str, str]
    zone_fares: ZonePairFares
    stop_coords: list[StopCoord]


class FareMeta(SerializableAsDict):
    """The bus-fares JSON's ``_meta`` block: the national single-fare cap."""

    # The national single-fare cap of the UK Gov Bus Fare Cap Scheme.
    NATIONAL_MAX_SINGLE_GBP: ClassVar[float] = 3.00

    national_max_single_gbp: float
    national_max_single_notes: str


def extract_operator_fares(operator: OperatorRef, options: FareExtractionOptions) -> OperatorFares | None:
    noc, display_name = operator.noc, operator.display_name
    datasets = get_bods_datasets(noc, options.api_key)
    if not datasets:
        logger.warning("No datasets found for NOC %s (%s)", noc, display_name)
        return None

    datasets = _filter_datasets_by_sub_operators(operator, datasets)
    if not datasets:
        logger.info("No matching datasets for %s after sub-operator filter", display_name)
        return None

    merged = _merge_dataset_results(datasets, options.stations, options.api_key, options.cached_only, options.naptan)
    combined_stop_coords = _dedupe_stop_coords(merged.stop_coords)
    combined_zones = _resolve_stop_zones(merged.zone_candidates, merged.fares)
    _apply_network_fares(merged.network_fares, combined_zones, merged.fares)

    if not combined_zones or not merged.fares:
        logger.info("No station-serving fare data for %s", display_name)
        return None

    logger.info(
        "Operator %s: processed %d datasets, %d stop->zone, %d zone pairs",
        display_name,
        merged.datasets_processed,
        len(combined_zones),
        len(merged.fares),
    )

    return OperatorFares(
        stop_zones=combined_zones, zone_fares=merged.fares, stop_coords=combined_stop_coords
    )


@dataclass(frozen=True)
class _MergeResults:
    """Accumulated BODS extraction results for one operator."""

    fares: ZonePairFares
    network_fares: list[NetworkFare]
    stop_coords: list[StopCoord]
    zone_candidates: StopZoneCandidates
    datasets_processed: int


def _filter_datasets_by_sub_operators(operator: OperatorRef, datasets: list[BodsDataset]) -> list[BodsDataset]:
    sub_ops = operator.sub_operators
    if sub_ops:
        filtered: list[BodsDataset] = []
        for ds in datasets:
            desc = (ds.get("description", "") or "").strip()
            if any(dataset_description_matches(desc, sub_op) for sub_op in sub_ops):
                filtered.append(ds)
            else:
                logger.info(
                    "Skipping dataset %s (%s) for %s — does not match sub-operators %s",
                    ds.get("id"),
                    desc,
                    operator.noc,
                    sub_ops,
                )
        datasets = filtered
        logger.info(
            "NOC %s: %d datasets remain after sub-operator filter",
            operator.noc,
            len(datasets),
        )
    return datasets


def _merge_dataset_results(
    datasets: list[BodsDataset],
    stations: list[Station],
    api_key: str,
    cached_only: bool,
    naptan: NaptanStops | None,
) -> _MergeResults:
    combined_fares: ZonePairFares = {}
    combined_network_fares: list[NetworkFare] = []
    combined_stop_coords: list[StopCoord] = []
    datasets_processed = 0
    zone_candidates: StopZoneCandidates = {}

    for ds in datasets:
        ds_id = ds.get("id")
        if not ds_id:
            continue
        time.sleep(1)

        had_any = False
        for xml_str in download_dataset(ds_id, api_key, cached_only=cached_only):
            had_any = True
            result = parse_netex_fares(xml_str, stations, naptan=naptan)
            del xml_str
            gc.collect()
            if result is None:
                continue
            datasets_processed += 1
            file_zones = result.stop_zones
            file_fares = result.zone_fares
            file_fare_zones = set()
            for k in file_fares:
                file_fare_zones.add(k.split(":")[0])
                file_fare_zones.add(k.split(":")[1])
            for stop_name, zone in file_zones.items():
                if stop_name not in zone_candidates:
                    zone_candidates[stop_name] = {}
                zone_candidates[stop_name][zone] = zone in file_fare_zones
            for key, fares in file_fares.items():
                if key not in combined_fares:
                    combined_fares[key] = {}
                combined_fares[key].update(fares)
            combined_network_fares.extend(nf for nf in result.network_fares if nf.covered_stops)
            combined_stop_coords.extend(result.stop_coords)
        del result
        if not had_any:
            logger.warning("No XML content yielded for dataset %d", ds_id)

    return _MergeResults(
        fares=combined_fares,
        network_fares=combined_network_fares,
        stop_coords=combined_stop_coords,
        zone_candidates=zone_candidates,
        datasets_processed=datasets_processed,
    )


def _dedupe_stop_coords(combined_stop_coords: list[StopCoord]) -> list[StopCoord]:
    seen: set[tuple[str, float, float]] = set()
    deduped: list[StopCoord] = []
    for c in combined_stop_coords:
        k = (
            c.name,
            round(c.lat, FareExtractionOptions.COORD_ROUND_DIGITS),
            round(c.lon, FareExtractionOptions.COORD_ROUND_DIGITS),
        )
        if k not in seen:
            seen.add(k)
            deduped.append(c)
    return deduped


def _resolve_stop_zones(
    zone_candidates: StopZoneCandidates,
    combined_fares: ZonePairFares,
) -> dict[str, str]:
    fare_zones = set()
    for k in combined_fares:
        fare_zones.add(k.split(":")[0])
        fare_zones.add(k.split(":")[1])
    combined_zones: dict[str, str] = {}
    for stop_name, zones in zone_candidates.items():
        best = next((z for z, has in zones.items() if has), None)
        if best is None:
            best = next(iter(zones))
        if best in fare_zones:
            combined_zones[stop_name] = best
    return combined_zones


def _apply_network_fares(
    combined_network_fares: list[NetworkFare],
    combined_zones: dict[str, str],
    combined_fares: ZonePairFares,
) -> None:
    for nf in combined_network_fares:
        covered_stops = nf.covered_stops
        if not covered_stops:
            continue
        covered_zones = {zone for stop_name, zone in combined_zones.items() if stop_name in covered_stops}
        if len(covered_zones) < 2:
            continue
        for key in list(combined_fares):
            z1, z2 = key.split(":")
            if z1 in covered_zones and z2 in covered_zones and nf.product_type not in combined_fares[key]:
                combined_fares[key][nf.product_type] = nf.price


def main():
    parser = argparse.ArgumentParser(description="Extract BODS bus fare data")
    parser.add_argument("--cached-only", action="store_true", help="Use cached files only, skip HTTP downloads")
    parser.add_argument("--force", action="store_true", help="Re-process all operators, ignoring checkpoints")
    args = parser.parse_args()

    api_key = os.environ.get("BUS_DATA_API_KEY", "")
    if _abort_if_unset(api_key, "BUS_DATA_API_KEY is not set"):
        return

    logger.info("Loading stations from %s", STATIONS_CSV)
    stations = load_stations()
    if _abort_if_unset(stations, "No stations loaded — check stations.csv exists"):
        return

    _BodsFares.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    _BodsFares.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    naptan = load_naptan_stops()
    fare_options = FareExtractionOptions(
        stations=stations,
        api_key=api_key,
        cached_only=args.cached_only,
        naptan=naptan,
    )

    all_operator_data: dict[str, Any] = {}
    all_operator_data["_meta"] = FareMeta(
        national_max_single_gbp=FareMeta.NATIONAL_MAX_SINGLE_GBP,
        national_max_single_notes="UK Gov Bus Fare Cap Scheme — applies to all participating operators in England",
    ).to_dict()

    for operator in FareExtractionOptions.OPERATORS:
        ckpt = checkpoint_path(operator.display_name)
        if ckpt.is_file() and not args.force:
            logger.info("Checkpoint exists for %s — skipping (use --force to re-process)", operator.display_name)
            with ckpt.open() as f:
                all_operator_data[operator.display_name] = json.load(f)
            gc.collect()
            continue

        logger.info("Processing %s (%s)...", operator.display_name, operator.noc)
        fare_result, failure = capture(partial(extract_operator_fares, operator, fare_options))
        if failure is not None:
            logger.error("Failed to extract for %s: %s", operator.display_name, failure)
            continue
        if fare_result:
            op_data = fare_result.to_dict()
            all_operator_data[operator.display_name] = op_data
            with ckpt.open("w") as f:
                json.dump(op_data, f, indent=2)
            logger.info("Extracted data for %s, checkpoint saved", operator.display_name)
        else:
            logger.info("No data extracted for %s (no station-serving routes)", operator.display_name)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    operator_count = len(all_operator_data) - 1
    if operator_count > 0:
        with OUTPUT_PATH.open("w") as f:
            json.dump(all_operator_data, f, indent=2)
        logger.info("Wrote bus fare data to %s (%d operators)", OUTPUT_PATH, operator_count)
    else:
        logger.info("No operator data extracted — skipping write (preserving existing %s)", OUTPUT_PATH)


if __name__ == "__main__":
    main()

