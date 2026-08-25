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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.download_bus_fares import (
    CACHE_DIR,
    CHECKPOINT_DIR,
    checkpoint_path,
    download_dataset,
    get_bods_datasets,
)
from scripts.parse_netex_fares import (
    NATIONAL_MAX_SINGLE_GBP,
    STATIONS_CSV,
    Station,
    dataset_description_matches,
    load_naptan_stops,
    load_stations,
    parse_netex_fares,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_PATH = Path("data/bus_fares.json")
COORD_ROUND_DIGITS = 4


@dataclass(frozen=True)
class OperatorRef:
    """One BODS operator: its NOC code and human-readable display name."""

    noc: str
    display_name: str


@dataclass(frozen=True)
class FareExtractionOptions:
    """The harness context every operator extraction shares: the station
    list, the BODS API key, and the cache/coordinate policies."""

    stations: list[Station]
    api_key: str
    cached_only: bool = False
    naptan: dict[str, tuple[float, float]] | None = None


OPERATORS: list[tuple[str, str]] = [
    ("SCSO", "Stagecoach_South"),
    ("SCSO", "Stagecoach_South_East"),
    ("SCOX", "Stagecoach_Oxfordshire"),
    ("SCEM", "Stagecoach_East_Midlands"),
    ("READ", "Reading_Buses"),
    ("METR", "Metrobus"),
    ("ABSS", "Abellio"),
    ("GALD", "Go_Ahead_London"),
]

NOC_SUB_OPERATORS: dict[str, list[str]] = {
    "Stagecoach_South": ["Stagecoach South"],
    "Stagecoach_South_East": ["Stagecoach South East"],
    "Stagecoach_Oxfordshire": ["Stagecoach Oxfordshire"],
    "Stagecoach_East_Midlands": ["Stagecoach East Midlands"],
    "Reading_Buses": ["Reading"],
    "Metrobus": ["Metrobus"],
    "Abellio": ["Abellio"],
    "Go_Ahead_London": ["Go-Ahead London", "Fastrack"],
}


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def extract_operator_fares(operator: OperatorRef, options: FareExtractionOptions) -> dict | None:
    noc, display_name = operator.noc, operator.display_name
    datasets = get_bods_datasets(noc, options.api_key)
    if not datasets:
        logger.warning("No datasets found for NOC %s (%s)", noc, display_name)
        return None

    datasets = _filter_datasets_by_sub_operators(noc, display_name, datasets)
    if not datasets:
        logger.info("No matching datasets for %s after sub-operator filter", display_name)
        return None

    combined_fares, combined_network_fares, combined_stop_coords, zone_candidates, datasets_processed = (
        _merge_dataset_results(datasets, options.stations, options.api_key, options.cached_only, options.naptan)
    )
    combined_stop_coords = _dedupe_stop_coords(combined_stop_coords)
    combined_zones = _resolve_stop_zones(zone_candidates, combined_fares)
    _apply_network_fares(combined_network_fares, combined_zones, combined_fares)

    if not combined_zones or not combined_fares:
        logger.info("No station-serving fare data for %s", display_name)
        return None

    logger.info(
        "Operator %s: processed %d datasets, %d stop->zone, %d zone pairs",
        display_name,
        datasets_processed,
        len(combined_zones),
        len(combined_fares),
    )

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {"stop_zones": combined_zones, "zone_fares": combined_fares, "stop_coords": combined_stop_coords}


# lucidlint: ignore record-shape BODS dataset list — API boundary owns the shape
def _filter_datasets_by_sub_operators(noc: str, display_name: str, datasets: list[dict]) -> list[dict]:
    sub_ops = NOC_SUB_OPERATORS.get(display_name, [])
    if sub_ops:
        filtered: list[dict] = []
        for ds in datasets:
            desc = (ds.get("description", "") or "").strip()
            if any(dataset_description_matches(desc, sub_op) for sub_op in sub_ops):
                filtered.append(ds)
            else:
                logger.info(
                    "Skipping dataset %s (%s) for %s — does not match sub-operators %s",
                    ds.get("id"),
                    desc,
                    noc,
                    sub_ops,
                )
        datasets = filtered
        logger.info(
            "NOC %s: %d datasets remain after sub-operator filter",
            noc,
            len(datasets),
        )
    return datasets


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _merge_dataset_results(
    datasets: list[dict],
    stations: list[Station],
    api_key: str,
    cached_only: bool,
    naptan: dict[str, tuple[float, float]] | None,
) -> tuple[dict, list, list, dict, int]:
    combined_fares: dict[str, dict[str, float]] = {}
    combined_network_fares: list[dict] = []
    combined_stop_coords: list[dict] = []
    datasets_processed = 0
    zone_candidates: dict[str, dict[str, bool]] = {}

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
            file_zones = result.get("stop_zones", {})
            file_fares = result.get("zone_fares", {})
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
            file_network_fares: list[dict] = result.get("network_fares", [])
            combined_network_fares.extend(nf for nf in file_network_fares if nf.get("covered_stops"))
            file_coords: list[dict] = result.get("stop_coords", [])
            combined_stop_coords.extend(file_coords)
        del result
        if not had_any:
            logger.warning("No XML content yielded for dataset %d", ds_id)

    return combined_fares, combined_network_fares, combined_stop_coords, zone_candidates, datasets_processed


# lucidlint: ignore record-shape stop-coord records — serialization boundary owns the shape
def _dedupe_stop_coords(combined_stop_coords: list[dict]) -> list[dict]:
    seen: set[tuple[str, float, float]] = set()
    deduped: list[dict] = []
    for c in combined_stop_coords:
        k = (c.get("name", ""), round(c.get("lat", 0), COORD_ROUND_DIGITS), round(c.get("lon", 0), COORD_ROUND_DIGITS))
        if k not in seen:
            seen.add(k)
            deduped.append(c)
    return deduped


# lucidlint: ignore record-shape zone-candidate map — wire-format dict (coding-standards.md)
def _resolve_stop_zones(
    zone_candidates: dict[str, dict[str, bool]],
    combined_fares: dict[str, dict[str, float]],
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


# lucidlint: ignore record-shape network-fare records — serialization boundary owns the shape
def _apply_network_fares(
    combined_network_fares: list[dict],
    combined_zones: dict[str, str],
    combined_fares: dict[str, dict[str, float]],
) -> None:
    for nf in combined_network_fares:
        covered_stops = nf.get("covered_stops", set())
        if not covered_stops:
            continue
        covered_zones = {zone for stop_name, zone in combined_zones.items() if stop_name in covered_stops}
        if len(covered_zones) < 2:
            continue
        for key in list(combined_fares):
            z1, z2 = key.split(":")
            if z1 in covered_zones and z2 in covered_zones and nf["product_type"] not in combined_fares[key]:
                combined_fares[key][nf["product_type"]] = nf["price"]


def main():
    parser = argparse.ArgumentParser(description="Extract BODS bus fare data")
    parser.add_argument("--cached-only", action="store_true", help="Use cached files only, skip HTTP downloads")
    parser.add_argument("--force", action="store_true", help="Re-process all operators, ignoring checkpoints")
    args = parser.parse_args()

    api_key = os.environ.get("BUS_DATA_API_KEY", "")
    if not api_key:
        logger.error("BUS_DATA_API_KEY is not set")
        return

    logger.info("Loading stations from %s", STATIONS_CSV)
    stations = load_stations()
    if not stations:
        logger.error("No stations loaded — check stations.csv exists")
        return

    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    naptan = load_naptan_stops()
    fare_options = FareExtractionOptions(
        stations=stations,
        api_key=api_key,
        cached_only=args.cached_only,
        naptan=naptan,
    )

    all_operator_data: dict[str, Any] = {}
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    all_operator_data["_meta"] = {
        "national_max_single_gbp": NATIONAL_MAX_SINGLE_GBP,
        "national_max_single_notes": "UK Gov Bus Fare Cap Scheme — applies to all participating operators in England",
    }

    for noc, display_name in OPERATORS:
        ckpt = checkpoint_path(display_name)
        if ckpt.is_file() and not args.force:
            logger.info("Checkpoint exists for %s — skipping (use --force to re-process)", display_name)
            with ckpt.open() as f:
                all_operator_data[display_name] = json.load(f)
            gc.collect()
            continue

        logger.info("Processing %s (%s)...", display_name, noc)
        try:
            op_data = extract_operator_fares(OperatorRef(noc, display_name), fare_options)
            if op_data:
                all_operator_data[display_name] = op_data
                with ckpt.open("w") as f:
                    json.dump(op_data, f, indent=2)
                logger.info("Extracted data for %s, checkpoint saved", display_name)
            else:
                logger.info("No data extracted for %s (no station-serving routes)", display_name)
        # lucidlint: ignore broad-except deliberate broad catch — boundary/fallback per coding-standards.md
        except Exception as e:
            logger.error("Failed to extract for %s: %s", display_name, e)
            continue

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
