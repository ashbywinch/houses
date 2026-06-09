"""One-time BODS NeTEx fare data extraction script.

Downloads Bus Open Data Service NeTEx fare data for London commuter-belt
operators and extracts the fare model (zone structure + stop-to-zone
mappings + zone-pair prices) for routes that serve train stations.

Output: data/bus_fares.json — loaded at runtime for bus fare lookups.
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
import logging
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BODS_BASE = "https://data.bus-data.dft.gov.uk/api/v1/"
DOWNLOAD_BASE = "https://data.bus-data.dft.gov.uk"
CACHE_DIR = Path("data/bods_cache")
CHECKPOINT_DIR = Path("data/.bus_fares_checkpoints")
STATIONS_CSV = Path("data/stations.csv")
NAPTAN_CACHE = Path("data/bods_stops.csv")
OUTPUT_PATH = Path("data/bus_fares.json")

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

NATIONAL_MAX_SINGLE_GBP = 3.00


def _dataset_cache_path(dataset_id: int, filename: str) -> Path:
    name = filename.removesuffix(".xml").replace(" ", "_").replace("(", "").replace(")", "")
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)[:200]
    return CACHE_DIR / f"dataset_{dataset_id}_{safe}.xml"


def _first_found(*elements):
    for el in elements:
        if el is not None:
            return el
    return None


NS = {
    "netex": "http://www.netex.org.uk/netex",
    "fxc": "http://www.netex.org.uk/fxc",
}


def _unprefixed(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


@dataclass
class Station:
    name: str
    crs: str
    lat: float
    long: float


def load_stations() -> list[Station]:
    stations: list[Station] = []
    with STATIONS_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            try:
                stations.append(
                    Station(
                        name=row.get("stationName", "").strip(),
                        crs=row.get("crsCode", "").strip(),
                        lat=float(row["lat"]),
                        long=float(row["long"]),
                    )
                )
            except (ValueError, KeyError):
                continue
    logger.info("Loaded %d stations from %s", len(stations), STATIONS_CSV)
    return stations


NAPTAN_DOWNLOAD = "https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=csv"


def _load_naptan_stops() -> dict[str, tuple[float, float]] | None:
    naptan: dict[str, tuple[float, float]] = {}

    if NAPTAN_CACHE.is_file():
        logger.info("Loading NaPTAN stops from %s", NAPTAN_CACHE)
        with NAPTAN_CACHE.open(newline="") as f:
            for row in csv.DictReader(f):
                atco = row.get("ATCOCode", "").strip()
                lat_raw = row.get("Latitude", "").strip()
                lon_raw = row.get("Longitude", "").strip()
                if atco and lat_raw and lon_raw:
                    try:
                        naptan[atco] = (float(lat_raw), float(lon_raw))
                    except ValueError:
                        continue
        logger.info("Loaded %d NaPTAN stop coordinates", len(naptan))
        return naptan

    logger.info("Downloading NaPTAN stop data from %s (101MB)", NAPTAN_DOWNLOAD)
    try:
        resp = httpx.get(NAPTAN_DOWNLOAD, timeout=300, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Failed to download NaPTAN data: %s", e)
        return None

    NAPTAN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with NAPTAN_CACHE.open("wb") as f:
        f.write(resp.content)

    for row in csv.DictReader(resp.text.splitlines()):
        atco = row.get("ATCOCode", "").strip()
        lat_raw = row.get("Latitude", "").strip()
        lon_raw = row.get("Longitude", "").strip()
        if atco and lat_raw and lon_raw:
            try:
                naptan[atco] = (float(lat_raw), float(lon_raw))
            except ValueError:
                continue
    logger.info("Downloaded and loaded %d NaPTAN stop coordinates", len(naptan))
    return naptan


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat_r = math.radians(lat2 - lat1)
    dlon_r = math.radians(lon2 - lon1)
    a = math.sin(dlat_r / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon_r / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _build_station_grid(stations: list[Station]) -> list[list[list[Station]]]:
    grid: list[list[list[Station]]] = [[[] for _ in range(36)] for _ in range(18)]
    for s in stations:
        col = int((s.long + 7.5) / 0.5)
        row = int((s.lat - 49.5) / 0.5)
        if 0 <= col < 36 and 0 <= row < 18:
            grid[row][col].append(s)
    logger.info("Built station grid (%d×%d cells)", len(grid), len(grid[0]))
    return grid


STATION_GRID: list[list[list[Station]]] = []


def is_near_station(lat: float, lon: float, stations: list[Station], max_dist_km: float = 0.2) -> bool:
    global STATION_GRID
    if not STATION_GRID:
        STATION_GRID = _build_station_grid(stations)
    col = int((lon + 7.5) / 0.5)
    row = int((lat - 49.5) / 0.5)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            r, c = row + dr, col + dc
            if 0 <= r < 18 and 0 <= c < 36:
                for s in STATION_GRID[r][c]:
                    if haversine_km(lat, lon, s.lat, s.long) <= max_dist_km:
                        return True
    return False


def get_bods_datasets(noc: str, api_key: str) -> list[dict]:
    url = f"{BODS_BASE}fares/dataset/"
    params: dict[str, Any] = {"noc": noc, "limit": 50, "api_key": api_key}

    resp = httpx.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])
    logger.info("NOC %s: %d fare datasets found", noc, len(results))
    return results


def download_dataset(dataset_id: int, api_key: str, cached_only: bool = False) -> Generator[str, None, None]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if cached_only:
        cached_paths = sorted(CACHE_DIR.glob(f"dataset_{dataset_id}.xml"))
        cached_paths.extend(sorted(CACHE_DIR.glob(f"dataset_{dataset_id}_*.xml")))
        if not cached_paths:
            logger.warning("No cached files found for dataset %d", dataset_id)
            return
        logger.info("Reading %d cached files for dataset %d", len(cached_paths), dataset_id)
        for path in cached_paths:
            yield path.read_text(encoding="utf-8")
        return

    url = f"{DOWNLOAD_BASE}/fares/dataset/{dataset_id}/download/"
    headers: dict[str, str] = {"Authorization": f"Token {api_key}"}

    resp = httpx.get(url, headers=headers, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    logger.info("Downloaded dataset %d (%d bytes)", dataset_id, len(resp.content))

    content = resp.content
    content_type = resp.headers.get("content-type", "")

    if "zip" in content_type or content[:2] == b"PK":
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            xml_names = sorted([n for n in zf.namelist() if n.endswith(".xml")])
            if not xml_names:
                logger.warning("No XML files found in zip for dataset %d", dataset_id)
                return
            logger.info("Zip contains %d XML files for dataset %d", len(xml_names), dataset_id)
            for xml_name in xml_names:
                xml_str = zf.read(xml_name).decode("utf-8")
                cache_path = _dataset_cache_path(dataset_id, Path(xml_name).stem)
                if not cache_path.is_file():
                    cache_path.write_text(xml_str, encoding="utf-8")
                yield xml_str
    else:
        yield resp.text


def parse_netex_fares(xml_str: str, stations: list[Station], naptan: dict[str, tuple[float, float]] | None = None) -> dict | None:
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        logger.warning("XML parse error: %s", e)
        return None

    stops: dict[str, dict] = {}

    for ssp in root.iter():
        tag = _unprefixed(ssp.tag)
        if tag not in ("ScheduledStopPoint", "scheduledStopPoint"):
            continue
        name_el = _first_found(
            ssp.find(".//netex:Name", NS), ssp.find(".//netex:name", NS),
        )
        if name_el is None:
            continue
        name = (name_el.text or "").strip()
        if not name:
            continue

        atco_el = _first_found(
            ssp.find(".//netex:AtcoCode", NS), ssp.find(".//netex:atcoCode", NS),
        )
        atco = atco_el.text.strip() if atco_el is not None and atco_el.text else (
            ssp.get("id", "") or ""
        ).strip()
        if not atco:
            continue

        lat_el = _first_found(
            ssp.find(".//netex:Latitude", NS), ssp.find(".//netex:latitude", NS),
        )
        lon_el = _first_found(
            ssp.find(".//netex:Longitude", NS), ssp.find(".//netex:longitude", NS),
        )
        lat = float(lat_el.text) if lat_el is not None and lat_el.text else None
        lon = float(lon_el.text) if lon_el is not None and lon_el.text else None

        if lat is None and lon is None and naptan is not None:
            atco_key = atco.removeprefix("atco:")
            coords = naptan.get(atco_key)
            if coords is not None:
                lat, lon = coords

        if atco in stops:
            continue

        stops[atco] = {"name": name, "lat": lat, "lon": lon, "near_station": False}
        if lat is not None and lon is not None:
            if is_near_station(lat, lon, stations):
                stops[atco]["near_station"] = True

    if not stops:
        logger.warning("No stops found in NeTEx data")
        return None

    near_count = sum(1 for s in stops.values() if s.get("near_station"))
    stops_with_coords = sum(1 for s in stops.values() if s.get("lat") is not None and s.get("lon") is not None)

    if near_count == 0 and stops_with_coords > 0:
        logger.info("No stops near any station (%d stops have coordinates), skipping XML", stops_with_coords)
        return None

    if near_count == 0 and stops_with_coords == 0:
        logger.warning("No stop coordinates available — cannot verify station proximity, proceeding anyway")

    logger.info("Found %d total stops, %d near stations", len(stops), near_count)

    zones: dict[str, list[str]] = {}
    for zone_el in root.iter():
        tag = _unprefixed(zone_el.tag)
        if tag not in ("FareZone", "fareZone"):
            continue
        zone_id_el = _first_found(
            zone_el.find(".//netex:Id", NS), zone_el.find(".//netex:id", NS),
        )
        zone_id = zone_id_el.text.strip() if zone_id_el is not None and zone_id_el.text else (zone_el.get("id", "") or "").strip()
        members: list[str] = []
        for member in zone_el.iter():
            mt = _unprefixed(member.tag)
            if mt == "Member" or mt == "StopPointRef" or "ref" in mt.lower():
                ref = member.get("ref", "") or member.text or ""
                if ref in stops:
                    members.append(ref)
        if zone_id and members:
            zones[zone_id] = members

    stop_zones: dict[str, str] = {}
    for zone_id, members in zones.items():
        for atco in members:
            stop = stops.get(atco)
            if stop:
                normalized = stop["name"].strip().lower()
                if normalized not in stop_zones:
                    stop_zones[normalized] = zone_id

    logger.info("Parsed %d fare zones, %d stop->zone mappings", len(zones), len(stop_zones))

    zone_fares: dict[str, dict[str, float]] = {}
    network_fares: list[dict] = []

    dme_zone_pairs: dict[str, str] = {}
    for dme in root.iter():
        tag = _unprefixed(dme.tag)
        if tag not in ("DistanceMatrixElement", "distanceMatrixElement"):
            continue

        dme_id = dme.get("id", "")

        start_el = _first_found(
            dme.find(".//netex:StartTariffZoneRef", NS), dme.find(".//netex:startTariffZoneRef", NS),
            dme.find(".//netex:StartZoneRef", NS), dme.find(".//netex:startZoneRef", NS),
        )
        end_el = _first_found(
            dme.find(".//netex:EndTariffZoneRef", NS), dme.find(".//netex:endTariffZoneRef", NS),
            dme.find(".//netex:EndZoneRef", NS), dme.find(".//netex:endZoneRef", NS),
        )
        if start_el is None or end_el is None:
            continue

        start_zone = start_el.get("ref", "") or start_el.text or ""
        end_zone = end_el.get("ref", "") or end_el.text or ""
        if not start_zone or not end_zone:
            continue

        key = f"{start_zone}:{end_zone}"
        normalized_key = f"{start_zone}:{end_zone.replace('@alighting', '@boarding')}"
        if dme_id:
            dme_zone_pairs[dme_id] = key

        price_ref_el = _first_found(
            dme.find(".//netex:PriceGroupRef", NS), dme.find(".//netex:priceGroupRef", NS),
        )
        if price_ref_el is not None:
            price_group_ref = price_ref_el.get("ref", "") or price_ref_el.text or ""
            price = _find_price_for_group(root, price_group_ref)
            if price is not None:
                if normalized_key not in zone_fares:
                    zone_fares[normalized_key] = {"adult_single": price}

    for dmep in root.iter():
        tag = _unprefixed(dmep.tag)
        if tag not in ("DistanceMatrixElementPrice", "distanceMatrixElementPrice"):
            continue

        amount_el = dmep.find(".//netex:Amount", NS)
        if amount_el is None or not amount_el.text:
            continue
        try:
            price = float(amount_el.text)
        except ValueError:
            continue

        dme_ref_el = _first_found(
            dmep.find(".//netex:DistanceMatrixElementRef", NS),
            dmep.find(".//netex:distanceMatrixElementRef", NS),
        )
        if dme_ref_el is None:
            continue
        dme_ref = dme_ref_el.get("ref", "") or dme_ref_el.text or ""
        if not dme_ref:
            continue

        zone_key = dme_zone_pairs.get(dme_ref)
        if not zone_key:
            continue
        nk = zone_key.replace("@alighting", "@boarding")

        if nk not in zone_fares:
            zone_fares[nk] = {}
        if "adult_single" not in zone_fares[nk]:
            zone_fares[nk]["adult_single"] = price

    if b"PreassignedFareProduct" in xml_str.encode():
        _parse_fare_products(root, zone_fares)
    if b"FareTable" in xml_str.encode():
        _parse_fare_tables(root, dme_zone_pairs, zone_fares, network_fares)

    if not zone_fares:
        logger.warning("No zone pair prices found — returning zones without prices")

    logger.info("Parsed %d zone pair prices", len(zone_fares))

    stop_coords: list[dict] = []
    for zone_id, members in zones.items():
        for atco in members:
            stop = stops.get(atco)
            if stop and stop.get("lat") is not None and stop.get("lon") is not None:
                zone_name = stop_zones.get(stop["name"].strip().lower())
                if zone_name:
                    stop_coords.append({
                        "name": stop["name"],
                        "lat": round(stop["lat"], 5),
                        "lon": round(stop["lon"], 5),
                        "zone": zone_name,
                    })

    return {
        "stop_zones": stop_zones,
        "stop_coords": stop_coords,
        "zone_fares": zone_fares,
        "network_fares": network_fares,
    }


def _find_price_for_group(root: ET.Element, group_ref: str) -> float | None:
    for pg in root.iter():
        tag = _unprefixed(pg.tag)
        if tag not in ("PriceGroup", "priceGroup"):
            continue
        pg_id_el = pg.find(".//netex:id", NS)
        pg_id = pg_id_el.text if pg_id_el is not None else pg.get("id", pg.get("Id", ""))

        attrs = {**pg.attrib}
        pg_id = attrs.get("id", attrs.get("Id", attrs.get("{http://www.netex.org.uk/netex}id", "")))

        if pg_id != group_ref:
            continue

        for amt in pg.iter():
            atag = _unprefixed(amt.tag)
            if atag == "Amount":
                amt_el = amt.find(".//netex:Amount", NS) or amt
                try:
                    text = amt.text or ""
                    if text:
                        val_el = amt.find(".//netex:amount", NS) or amt.find("netex:Amount", NS)
                        if val_el is not None:
                            return float(val_el.text)
                        return float(text)
                except (ValueError, TypeError):
                    continue

    return None


def _parse_fare_products(root: ET.Element, zone_fares: dict[str, dict[str, float]]) -> None:
    for product in root.iter():
        tag = _unprefixed(product.tag)
        if tag not in ("PreassignedFareProduct", "preassignedFareProduct"):
            continue

        name_el = _first_found(
            product.find(".//netex:Name", NS), product.find(".//netex:name", NS),
        )
        if name_el is None:
            continue
        product_name = (name_el.text or "").strip().lower()

        product_type = None
        if "single" in product_name:
            product_type = "adult_single"
        elif "return" in product_name:
            product_type = "adult_return"
        elif "day" in product_name or "dayrider" in product_name or "day rider" in product_name:
            product_type = "adult_day"

        if product_type is None:
            continue

        price = _find_product_price(product)
        if price is not None:
            for dme in root.iter():
                dtag = _unprefixed(dme.tag)
                if dtag not in ("DistanceMatrixElement", "distanceMatrixElement"):
                    continue
                for pfep in dme.iter():
                    ptag = _unprefixed(pfep.tag)
                    if ptag == "PreassignedFareProductRef" or "fareProductRef" in ptag:
                        ref = pfep.get("ref", "")
                        product_id = None
                        for attr_key in ("id", "Id", "{http://www.netex.org.uk/netex}id"):
                            if attr_key in product.attrib:
                                product_id = product.attrib[attr_key]
                                break
                        if ref and product_id and (ref == product_id or ref in product_id):
                            start_ref = _first_found(
                                dme.find(".//netex:StartZoneRef", NS), dme.find(".//netex:startZoneRef", NS),
                            )
                            end_ref = _first_found(
                                dme.find(".//netex:EndZoneRef", NS), dme.find(".//netex:endZoneRef", NS),
                            )
                            if start_ref is not None and end_ref is not None:
                                sz = start_ref.get("ref", "") or start_ref.text or ""
                                ez = end_ref.get("ref", "") or end_ref.text or ""
                                key = f"{sz}:{ez}".replace("@alighting", "@boarding")
                                if key not in zone_fares:
                                    zone_fares[key] = {}
                                zone_fares[key][product_type] = price
            _associate_product_with_zones(root, product, product_type, price, zone_fares)


def _find_product_price(product: ET.Element) -> float | None:
    for child in product.iter():
        tag = _unprefixed(child.tag)
        if tag == "Price" or tag == "price":
            amt_el = _first_found(
                child.find(".//netex:Amount", NS), child.find("netex:Amount", NS),
            )
            if amt_el is not None:
                try:
                    return float(amt_el.text)
                except (ValueError, TypeError):
                    continue
            try:
                return float(child.text)
            except (ValueError, TypeError):
                continue
    return None


def _associate_product_with_zones(
    root: ET.Element,
    product: ET.Element,
    product_type: str,
    price: float,
    zone_fares: dict[str, dict[str, float]],
) -> None:
    product_id = None
    for attr_key in ("id", "Id", "{http://www.netex.org.uk/netex}id"):
        if attr_key in product.attrib:
            product_id = product.attrib[attr_key]
            break
    if not product_id:
        return

    for sop in root.iter():
        stag = _unprefixed(sop.tag)
        if stag not in ("SalesOfferPackage", "salesOfferPackage"):
            continue

        for ref in sop.iter():
            rtag = _unprefixed(ref.tag)
            if rtag in ("PreassignedFareProductRef", "fareProductRef", "fareProduct") or "productRef" in rtag:
                ref_val = ref.get("ref", "")
                if ref_val and ref_val == product_id:
                    for dme in root.iter():
                        dtag = _unprefixed(dme.tag)
                        if dtag not in ("DistanceMatrixElement", "distanceMatrixElement"):
                            continue
                        for dme_sop_ref in dme.iter():
                            ds_tag = _unprefixed(dme_sop_ref.tag)
                            if ds_tag in ("SalesOfferPackageRef", "sopRef"):
                                sop_ref = dme_sop_ref.get("ref", "")
                                sop_id = sop.get("id", sop.attrib.get("{http://www.netex.org.uk/netex}id", ""))
                                if sop_ref and sop_id and sop_ref == sop_id:
                                    start_ref = dme.find(".//netex:StartZoneRef", NS) or dme.find(".//netex:startZoneRef", NS)
                                    end_ref = dme.find(".//netex:EndZoneRef", NS) or dme.find(".//netex:endZoneRef", NS)
                                    if start_ref is not None and end_ref is not None:
                                        sz = start_ref.get("ref", "") or start_ref.text or ""
                                        ez = end_ref.get("ref", "") or end_ref.text or ""
                                        key = f"{sz}:{ez}".replace("@alighting", "@boarding")
                                        if key not in zone_fares:
                                            zone_fares[key] = {}
                                        zone_fares[key][product_type] = price
                    return


def _parse_fare_tables(
    root: ET.Element,
    dme_zone_pairs: dict[str, str],
    zone_fares: dict[str, dict[str, float]],
    network_fares: list[dict],
) -> None:
    products: dict[str, str] = {}
    for product in root.iter():
        tag = _unprefixed(product.tag)
        if tag not in ("PreassignedFareProduct", "preassignedFareProduct"):
            continue
        name_el = _first_found(
            product.find(".//netex:Name", NS), product.find(".//netex:name", NS),
        )
        if name_el is None:
            continue
        pname = (name_el.text or "").strip().lower()
        ptype: str | None = None
        if "single" in pname:
            ptype = "adult_single"
        elif "return" in pname:
            ptype = "adult_return"
        elif "day" in pname or "dayrider" in pname or "day rider" in pname:
            ptype = "adult_day"
        if ptype is None:
            continue
        pid = None
        for attr_key in ("id", "Id", "{http://www.netex.org.uk/netex}id"):
            if attr_key in product.attrib:
                pid = product.attrib[attr_key]
                break
        if pid:
            products[pid] = ptype

    for ft in root.iter():
        tag = _unprefixed(ft.tag)
        if tag not in ("FareTable", "fareTable"):
            continue
        product_id = None
        for pf_ref in ft.iter():
            rt = _unprefixed(pf_ref.tag)
            if rt == "PreassignedFareProductRef" or "fareProductRef" in rt:
                product_id = pf_ref.get("ref", "")
                break
        if not product_id or product_id not in products:
            continue
        ptype = products[product_id]
        for ft_child in ft.iter():
            ct = _unprefixed(ft_child.tag)
            if ct in ("DistanceMatrixElementPrice", "distanceMatrixElementPrice"):
                amt_el = _first_found(
                    ft_child.find(".//netex:Amount", NS), ft_child.find(".//netex:amount", NS),
                )
                if amt_el is None or not amt_el.text:
                    continue
                try:
                    price = float(amt_el.text)
                except ValueError:
                    continue
                dme_ref_el = _first_found(
                    ft_child.find(".//netex:DistanceMatrixElementRef", NS),
                    ft_child.find(".//netex:distanceMatrixElementRef", NS),
                )
                if dme_ref_el is not None:
                    dme_ref = dme_ref_el.get("ref", "") or dme_ref_el.text or ""
                    zone_key = dme_zone_pairs.get(dme_ref)
                    if zone_key:
                        nk = zone_key.replace("@alighting", "@boarding")
                        if nk not in zone_fares:
                            zone_fares[nk] = {}
                        zone_fares[nk][ptype] = price
                elif ptype in ("adult_day", "adult_return"):
                    covered_stops: set[str] = set()
                    for t in root.iter():
                        tt = _unprefixed(t.tag)
                        if tt == "Tariff":
                            for fz_ref in t.iter():
                                zt = _unprefixed(fz_ref.tag)
                                if zt == "FareZoneRef":
                                    zone_id = fz_ref.get("ref", "")
                                    if zone_id:
                                        for fz in root.iter():
                                            ftag = _unprefixed(fz.tag)
                                            if ftag == "FareZone" and fz.get("id", "") == zone_id:
                                                for m in fz.iter():
                                                    mt = _unprefixed(m.tag)
                                                    if "ref" in mt.lower() and m.text:
                                                        covered_stops.add(m.text.strip().lower())
                                    break
                            break
                    if covered_stops:
                        network_fares.append({
                            "price": price,
                            "product_type": ptype,
                            "covered_stops": covered_stops,
                        })


def _dataset_description_matches(desc: str, sub_op: str) -> bool:
    if not desc:
        return False
    return desc.strip().lower() == sub_op.lower()


def extract_operator_fares(
    noc: str,
    display_name: str,
    stations: list[Station],
    api_key: str,
    cached_only: bool = False,
    naptan: dict[str, tuple[float, float]] | None = None,
) -> dict | None:
    datasets = get_bods_datasets(noc, api_key)
    if not datasets:
        logger.warning("No datasets found for NOC %s (%s)", noc, display_name)
        return None

    sub_ops = NOC_SUB_OPERATORS.get(display_name, [])
    if sub_ops:
        filtered: list[dict] = []
        for ds in datasets:
            desc = (ds.get("description", "") or "").strip()
            if any(_dataset_description_matches(desc, sub_op) for sub_op in sub_ops):
                filtered.append(ds)
            else:
                logger.info(
                    "Skipping dataset %s (%s) for %s — does not match sub-operators %s",
                    ds.get("id"), desc, noc, sub_ops,
                )
        datasets = filtered
        logger.info(
            "NOC %s: %d datasets remain after sub-operator filter",
            noc, len(datasets),
        )

    if not datasets:
        logger.info("No matching datasets for %s after sub-operator filter", display_name)
        return None

    combined_zones: dict[str, str] = {}
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
            for nf in file_network_fares:
                if nf.get("covered_stops"):
                    combined_network_fares.append(nf)
            file_coords: list[dict] = result.get("stop_coords", [])
            combined_stop_coords.extend(file_coords)
        del result
        if not had_any:
            logger.warning("No XML content yielded for dataset %d", ds_id)

    seen: set[tuple[str, float, float]] = set()
    deduped: list[dict] = []
    for c in combined_stop_coords:
        k = (c.get("name", ""), round(c.get("lat", 0), 4), round(c.get("lon", 0), 4))
        if k not in seen:
            seen.add(k)
            deduped.append(c)
    combined_stop_coords = deduped
    del seen, deduped
    gc.collect()

    fare_zones = set()
    for k in combined_fares:
        fare_zones.add(k.split(":")[0])
        fare_zones.add(k.split(":")[1])
    for stop_name, zones in zone_candidates.items():
        best = next((z for z, has in zones.items() if has), None)
        if best is None:
            best = next(iter(zones))
        if best in fare_zones:
            combined_zones[stop_name] = best

    for nf in combined_network_fares:
        covered_stops = nf.get("covered_stops", set())
        if not covered_stops:
            continue
        covered_zones: set[str] = set()
        for stop_name, zone in combined_zones.items():
            if stop_name in covered_stops:
                covered_zones.add(zone)
        if len(covered_zones) < 2:
            continue
        for key in list(combined_fares):
            z1, z2 = key.split(":")
            if z1 in covered_zones and z2 in covered_zones:
                if nf["product_type"] not in combined_fares[key]:
                    combined_fares[key][nf["product_type"]] = nf["price"]

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

    return {"stop_zones": combined_zones, "zone_fares": combined_fares, "stop_coords": combined_stop_coords}


def _checkpoint_path(display_name: str) -> Path:
    safe_name = display_name.replace(" ", "_").replace("/", "_")
    return CHECKPOINT_DIR / f"{safe_name}.json"


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

    naptan = _load_naptan_stops()

    all_operator_data: dict[str, Any] = {}
    all_operator_data["_meta"] = {
        "national_max_single_gbp": NATIONAL_MAX_SINGLE_GBP,
        "national_max_single_notes": "UK Gov Bus Fare Cap Scheme — applies to all participating operators in England",
    }

    for noc, display_name in OPERATORS:
        ckpt = _checkpoint_path(display_name)
        if ckpt.is_file() and not args.force:
            logger.info("Checkpoint exists for %s — skipping (use --force to re-process)", display_name)
            with ckpt.open() as f:
                all_operator_data[display_name] = json.load(f)
            gc.collect()
            continue

        logger.info("Processing %s (%s)...", display_name, noc)
        try:
            op_data = extract_operator_fares(noc, display_name, stations, api_key, cached_only=args.cached_only, naptan=naptan)
            if op_data:
                all_operator_data[display_name] = op_data
                with ckpt.open("w") as f:
                    json.dump(op_data, f, indent=2)
                logger.info("Extracted data for %s, checkpoint saved", display_name)
            else:
                logger.info("No data extracted for %s (no station-serving routes)", display_name)
        except Exception as e:
            logger.error("Failed to extract for %s: %s", display_name, e)

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
