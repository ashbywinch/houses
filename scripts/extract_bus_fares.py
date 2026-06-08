"""One-time BODS NeTEx fare data extraction script.

Downloads Bus Open Data Service NeTEx fare data for London commuter-belt
operators and extracts the fare model (zone structure + stop-to-zone
mappings + zone-pair prices) for routes that serve train stations.

Output: data/bus_fares.json — loaded at runtime for bus fare lookups.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BODS_BASE = "https://data.bus-data.dft.gov.uk/api/v1/"
DOWNLOAD_BASE = "https://data.bus-data.dft.gov.uk"
CACHE_DIR = Path("data/bods_cache")
STATIONS_CSV = Path("data/stations.csv")
OUTPUT_PATH = Path("data/bus_fares.json")

# Operators to process: (NOC, display_name)
# NOCs from BODS; display name used as key in output JSON
OPERATORS: list[tuple[str, str]] = [
    ("SCSO", "Stagecoach_South"),
    ("SCSE", "Stagecoach_South_East"),
    ("SCOX", "Stagecoach_Oxfordshire"),
    ("SCEM", "Stagecoach_East_Midlands"),
    ("READ", "Reading_Buses"),
    ("METR", "Metrobus"),
    ("ABSS", "Abellio"),
    ("GALD", "Go_Ahead_London"),
]

NATIONAL_MAX_SINGLE_GBP = 3.00


def _first_found(*elements):
    """Return the first Element that is not None (avoids bool()-based ``x or y``
    pattern, which can give wrong results in Python < 3.14 where
    :class:`xml.etree.ElementTree.Element` may be falsy)."""
    for el in elements:
        if el is not None:
            return el
    return None

NS = {
    "netex": "http://www.netex.org.uk/netex",
    "fxc": "http://www.netex.org.uk/fxc",
}


def _unprefixed(tag: str) -> str:
    """Strip namespace prefix from an XML tag."""
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


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = (lat2 - lat1) * 3.14159 / 180
    dlon = (lon2 - lon1) * 3.14159 / 180
    a = (
        (dlat / 2) ** 2
    )  # simplified sin²(dlat/2) for small angles — approximation
    # Actually use the real formula:
    import math
    dlat_r = math.radians(lat2 - lat1)
    dlon_r = math.radians(lon2 - lon1)
    a = math.sin(dlat_r / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon_r / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def is_near_station(lat: float, lon: float, stations: list[Station], max_dist_km: float = 0.2) -> bool:
    for s in stations:
        if haversine_km(lat, lon, s.lat, s.long) <= max_dist_km:
            return True
    return False


def get_bods_datasets(noc: str, api_key: str = "") -> list[dict]:
    """Get fare datasets for a given operator NOC from BODS."""
    url = f"{BODS_BASE}fares/dataset/"
    params: dict[str, Any] = {"noc": noc, "limit": 50}
    if api_key:
        params["api_key"] = api_key

    resp = httpx.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])
    logger.info("NOC %s: %d fare datasets found", noc, len(results))
    return results


def download_dataset(dataset_id: int, noc: str = "", api_key: str = "") -> str | None:
    """Download BODS fare dataset, using local cache if available.

    Saves downloaded files to ``data/bods_cache/`` so subsequent runs skip
    the download. Returns the XML string or None on failure.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"dataset_{dataset_id}.xml"

    if cache_path.is_file():
        logger.info("Using cached dataset %d from %s", dataset_id, cache_path)
        return cache_path.read_text(encoding="utf-8")

    url = f"{DOWNLOAD_BASE}/fares/dataset/{dataset_id}/download/"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Token {api_key}"

    try:
        resp = httpx.get(url, headers=headers, timeout=120, follow_redirects=True)
        resp.raise_for_status()
        logger.info("Downloaded dataset %d (%d bytes)", dataset_id, len(resp.content))

        # Response may be zip or raw XML
        content = resp.content
        content_type = resp.headers.get("content-type", "")
        if "zip" in content_type or content[:2] == b"PK":
            import zipfile
            import io
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                xml_files = [n for n in zf.namelist() if n.endswith(".xml")]
                if not xml_files:
                    logger.warning("No XML files found in zip for dataset %d", dataset_id)
                    return None
                # Use the first (usually only) XML file
                xml_content = zf.read(xml_files[0]).decode("utf-8")
        else:
            xml_content = resp.text

        cache_path.write_text(xml_content, encoding="utf-8")
        logger.info("Cached to %s", cache_path)
        return xml_content
    except Exception as e:
        logger.warning("Failed to download dataset %d: %s", dataset_id, e)
        return None


def parse_netex_fares(xml_str: str, stations: list[Station]) -> dict | None:
    """Parse NeTEx XML and extract fare model for routes serving train stations.

    Returns dict in bus_fares.json format, or None if parsing fails.
    """
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        logger.warning("XML parse error: %s", e)
        return None

    # Map of ATCO code → stop name + coords
    stops: dict[str, dict] = {}

    # Parse ScheduledStopPoints — use name as identifier; AtcoCode
    # and coordinates are optional and may live in separate data frames.
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
        atco = atco_el.text.strip() if atco_el is not None and atco_el.text else name

        lat_el = _first_found(
            ssp.find(".//netex:Latitude", NS), ssp.find(".//netex:latitude", NS),
        )
        lon_el = _first_found(
            ssp.find(".//netex:Longitude", NS), ssp.find(".//netex:longitude", NS),
        )
        lat = float(lat_el.text) if lat_el is not None and lat_el.text else None
        lon = float(lon_el.text) if lon_el is not None and lon_el.text else None

        if atco in stops:
            continue

        stops[atco] = {"name": name, "lat": lat, "lon": lon, "near_station": False}
        if lat is not None and lon is not None:
            if is_near_station(lat, lon, stations):
                stops[atco]["near_station"] = True

    if not stops:
        logger.warning("No stops found in NeTEx data")
        return None

    # Check if any stops are near a train station (for station-serving filtering)
    near_count = sum(1 for s in stops.values() if s.get("near_station"))

    if not stops:
        logger.warning("No stops found in NeTEx data")
        return None

    if near_count == 0:
        logger.info("No stop coordinates available — cannot verify station proximity, proceeding anyway")

    logger.info("Found %d total stops, %d near stations", len(stops), near_count)

    # Parse FareZones: zone id → list of stop ATCO codes
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
                ref = member.text or ""
                if ref in stops:
                    members.append(ref)
        if zone_id and members:
            zones[zone_id] = members

    # Build stop_name → zone_id mapping
    stop_zones: dict[str, str] = {}
    for zone_id, members in zones.items():
        for atco in members:
            stop = stops.get(atco)
            if stop:
                normalized = stop["name"].strip().lower()
                # Only keep the first zone mapping for a stop
                if normalized not in stop_zones:
                    stop_zones[normalized] = zone_id

    logger.info("Parsed %d fare zones, %d stop→zone mappings", len(zones), len(stop_zones))

    # Parse distance matrix / zone pair → price
    zone_fares: dict[str, dict[str, float]] = {}

    # Phase 1: Collect all DistanceMatrixElement zone pairs keyed by element id
    dme_zone_pairs: dict[str, str] = {}
    for dme in root.iter():
        tag = _unprefixed(dme.tag)
        if tag not in ("DistanceMatrixElement", "distanceMatrixElement"):
            continue

        dme_id = dme.get("id", "")

        # Try StartTariffZoneRef/EndTariffZoneRef (real BODS format) first,
        # then fall back to StartZoneRef/EndZoneRef (AC Williams format)
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
        if dme_id:
            dme_zone_pairs[dme_id] = key
        # Also try PriceGroupRef path (AC Williams format)
        price_ref_el = _first_found(
            dme.find(".//netex:PriceGroupRef", NS), dme.find(".//netex:priceGroupRef", NS),
        )
        if price_ref_el is not None:
            price_group_ref = price_ref_el.get("ref", "") or price_ref_el.text or ""
            price = _find_price_for_group(root, price_group_ref)
            if price is not None:
                if key not in zone_fares:
                    zone_fares[key] = {"adult_single": price}

    # Phase 2: Find DistanceMatrixElementPrice elements (real BODS format)
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

        if zone_key not in zone_fares:
            zone_fares[zone_key] = {}
        if "adult_single" not in zone_fares[zone_key]:
            zone_fares[zone_key]["adult_single"] = price

    # Also try to find PreassignedFareProduct prices
    _parse_fare_products(root, zone_fares)

    if not zone_fares:
        logger.warning("No zone pair prices found — returning zones without prices")

    logger.info("Parsed %d zone pair prices", len(zone_fares))

    # Build stop_coords index for spatial matching
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
    }


def _find_price_for_group(root: ET.Element, group_ref: str) -> float | None:
    """Find the GBP amount for a PriceGroup reference."""
    for pg in root.iter():
        tag = _unprefixed(pg.tag)
        if tag not in ("PriceGroup", "priceGroup"):
            continue
        pg_id_el = pg.find(".//netex:id", NS)
        pg_id = pg_id_el.text if pg_id_el is not None else pg.get("id", pg.get("Id", ""))

        # Also check the ref attribute
        attrs = {**pg.attrib}
        pg_id = attrs.get("id", attrs.get("Id", attrs.get("{http://www.netex.org.uk/netex}id", "")))

        if pg_id != group_ref:
            continue

        # Find Amount elements
        for amt in pg.iter():
            atag = _unprefixed(amt.tag)
            if atag == "Amount":
                amt_el = amt.find(".//netex:Amount", NS) or amt
                try:
                    text = amt.text or ""
                    if text:
                        # Try to parse as the price value
                        val_el = amt.find(".//netex:amount", NS) or amt.find("netex:Amount", NS)
                        if val_el is not None:
                            return float(val_el.text)
                        return float(text)
                except (ValueError, TypeError):
                    continue

    return None


def _parse_fare_products(root: ET.Element, zone_fares: dict[str, dict[str, float]]) -> None:
    """Parse PreassignedFareProduct elements for ticket-type info."""
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

        # Determine product type
        product_type = None
        if "single" in product_name:
            product_type = "adult_single"
        elif "return" in product_name:
            product_type = "adult_return"
        elif "day" in product_name or "dayrider" in product_name or "day rider" in product_name:
            product_type = "adult_day"

        if product_type is None:
            continue

        # Find price
        price = _find_product_price(product)
        if price is not None:
            # Associate with zone pairs
            # Find which lines/link this product belongs to
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
                                key = f"{sz}:{ez}"
                                if key not in zone_fares:
                                    zone_fares[key] = {}
                                zone_fares[key][product_type] = price
            # Also try to find via the more direct approach
            # Search for product references in distance matrix elements
            _associate_product_with_zones(root, product, product_type, price, zone_fares)


def _find_product_price(product: ET.Element) -> float | None:
    """Extract the GBP price from a PreassignedFareProduct element."""
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
            # Try direct text
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
    """Associate a fare product price with the correct zone pairs."""
    # Find the product ID
    product_id = None
    for attr_key in ("id", "Id", "{http://www.netex.org.uk/netex}id"):
        if attr_key in product.attrib:
            product_id = product.attrib[attr_key]
            break
    if not product_id:
        return

    # Look for this product in distance matrix elements via SalesOfferPackage
    for sop in root.iter():
        stag = _unprefixed(sop.tag)
        if stag not in ("SalesOfferPackage", "salesOfferPackage"):
            continue

        # Check if this SOP references our product
        for ref in sop.iter():
            rtag = _unprefixed(ref.tag)
            if rtag in ("PreassignedFareProductRef", "fareProductRef", "fareProduct") or "productRef" in rtag:
                ref_val = ref.get("ref", "")
                if ref_val and ref_val == product_id:
                    # Found the SOP for our product — find which DME it links to
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
                                        key = f"{sz}:{ez}"
                                        if key not in zone_fares:
                                            zone_fares[key] = {}
                                        zone_fares[key][product_type] = price
                    return


def extract_operator_fares(
    noc: str,
    display_name: str,
    stations: list[Station],
    api_key: str = "",
) -> dict | None:
    """Extract fare model for a single BODS operator."""
    datasets = get_bods_datasets(noc, api_key)
    if not datasets:
        logger.warning("No datasets found for NOC %s (%s)", noc, display_name)
        return None

    combined_zones: dict[str, str] = {}
    combined_fares: dict[str, dict[str, float]] = {}
    datasets_processed = 0

    for ds in datasets:
        ds_id = ds.get("id")
        if not ds_id:
            continue
        # Rate limit: space out requests
        time.sleep(1)

        xml_str = download_dataset(ds_id, noc, api_key)
        if xml_str is None:
            continue
        result = parse_netex_fares(xml_str, stations)
        if result is None:
            continue
        datasets_processed += 1
        combined_zones.update(result.get("stop_zones", {}))
        for key, fares in result.get("zone_fares", {}).items():
            if key not in combined_fares:
                combined_fares[key] = {}
            combined_fares[key].update(fares)

    if not combined_zones or not combined_fares:
        logger.info("No station-serving fare data for %s", display_name)
        return None

    logger.info(
        "Operator %s: processed %d datasets, %d stop→zone, %d zone pairs",
        display_name,
        datasets_processed,
        len(combined_zones),
        len(combined_fares),
    )

    return {"stop_zones": combined_zones, "zone_fares": combined_fares}


def main():
    api_key = ""
    # Try to read BODS API key from env or .env
    import os
    from pathlib import Path

    env_path = Path(".env")
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            if line.startswith("BODS_API_KEY="):
                api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not api_key:
        api_key = os.environ.get("BODS_API_KEY", "")

    if not api_key:
        api_key = os.environ.get("BUS_DATA_API_KEY", "")

    logger.info("Loading stations from %s", STATIONS_CSV)
    stations = load_stations()
    if not stations:
        logger.error("No stations loaded — check stations.csv exists")
        return

    all_operator_data: dict[str, Any] = {}
    all_operator_data["_meta"] = {
        "national_max_single_gbp": NATIONAL_MAX_SINGLE_GBP,
        "national_max_single_notes": "UK Gov Bus Fare Cap Scheme — applies to all participating operators in England",
    }

    for noc, display_name in OPERATORS:
        logger.info("Processing %s (%s)...", display_name, noc)
        try:
            op_data = extract_operator_fares(noc, display_name, stations, api_key)
            if op_data:
                all_operator_data[display_name] = op_data
                logger.info("Extracted data for %s", display_name)
            else:
                logger.info("No data extracted for %s (no station-serving routes)", display_name)
        except Exception as e:
            logger.error("Failed to extract for %s: %s", display_name, e)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Only write if we found at least one operator with data (avoid clobbering
    # the existing file on a partial/failed run)
    operator_count = len(all_operator_data) - 1  # exclude _meta
    if operator_count > 0:
        with OUTPUT_PATH.open("w") as f:
            json.dump(all_operator_data, f, indent=2)
        logger.info("Wrote bus fare data to %s (%d operators)", OUTPUT_PATH, operator_count)
    else:
        logger.info("No operator data extracted — skipping write (preserving existing %s)", OUTPUT_PATH)


if __name__ == "__main__":
    main()
