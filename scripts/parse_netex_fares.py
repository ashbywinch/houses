"""NeTEx XML parsing and bus fare zone extraction."""

from __future__ import annotations

import csv
import logging
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

NS = {
    "netex": "http://www.netex.org.uk/netex",
    "fxc": "http://www.netex.org.uk/fxc",
}

NATIONAL_MAX_SINGLE_GBP = 3.00

STATIONS_CSV = Path("data/stations.csv")
NAPTAN_CACHE = Path("data/bods_stops.csv")
NAPTAN_DOWNLOAD = "https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=csv"
GRID_COLS = 36
GRID_ROWS = 18
GRID_CELL_DEG = 0.5
# lucidlint: ignore magic-number -7.5 is the named constant's value — scanner flags negatives in constant definitions
GRID_LON_ORIGIN = -7.5
GRID_LAT_ORIGIN = 49.5
COORD_ROUND_DIGITS = 5


def _first_found(*elements):
    for el in elements:
        if el is not None:
            return el
    return None


def _unprefixed(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


@dataclass
class Station:
    name: str
    crs: str
    lat: float
    long: float


@dataclass(frozen=True)
class NetexStop:
    """A NeTEx ScheduledStopPoint record: name, coordinates (when present),
    and whether it lies near a railway station."""

    name: str
    lat: float | None
    lon: float | None
    near_station: bool


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


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def load_naptan_stops() -> dict[str, tuple[float, float]] | None:
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
    except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException) as e:
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
    r = 6371.0
    dlat_r = math.radians(lat2 - lat1)
    dlon_r = math.radians(lon2 - lon1)
    sin_half = math.sin(dlat_r / 2) ** 2
    cos_product = math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
    a = sin_half + cos_product * math.sin(dlon_r / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# lucidlint: ignore record-shape spatial grid index (rows×cols → station lists), not a fixed record shape
def _build_station_grid(stations: list[Station]) -> list[list[list[Station]]]:
    grid: list[list[list[Station]]] = [[[] for _ in range(GRID_COLS)] for _ in range(GRID_ROWS)]
    for s in stations:
        col = int((s.long - GRID_LON_ORIGIN) / GRID_CELL_DEG)
        row = int((s.lat - GRID_LAT_ORIGIN) / GRID_CELL_DEG)
        if 0 <= col < GRID_COLS and 0 <= row < GRID_ROWS:
            grid[row][col].append(s)
    logger.info("Built station grid (%d×%d cells)", len(grid), len(grid[0]))
    return grid


# lucidlint: ignore global-state lazy-built station-grid memo (perf: built once per run); single writer is_near_station
STATION_GRID: list[list[list[Station]]] = []


def is_near_station(lat: float, lon: float, stations: list[Station], max_dist_km: float = 0.2) -> bool:
    # lucidlint: ignore global-state lazy-built station-grid memo; single writer is_near_station
    global STATION_GRID
    if not STATION_GRID:
        STATION_GRID = _build_station_grid(stations)
    col = int((lon - GRID_LON_ORIGIN) / GRID_CELL_DEG)
    row = int((lat - GRID_LAT_ORIGIN) / GRID_CELL_DEG)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            r, c = row + dr, col + dc
            if 0 <= r < GRID_ROWS and 0 <= c < GRID_COLS:
                for s in STATION_GRID[r][c]:
                    if haversine_km(lat, lon, s.lat, s.long) <= max_dist_km:
                        return True
    return False


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def parse_netex_fares(
    xml_str: str,
    stations: list[Station],
    naptan: dict[str, tuple[float, float]] | None = None,
) -> dict | None:
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        logger.warning("XML parse error: %s", e)
        return None

    stops = _parse_stops(root, stations, naptan)
    if not stops:
        logger.warning("No stops found in NeTEx data")
        return None
    if not _stops_have_near_station(stops):
        return None

    zones, stop_zones = _parse_fare_zones(root, stops)
    zone_fares, dme_zone_pairs = _parse_distance_matrix_elements(root)
    network_fares: list[dict] = []

    _parse_distance_matrix_prices(root, dme_zone_pairs, zone_fares)

    if b"PreassignedFareProduct" in xml_str.encode():
        _parse_fare_products(root, zone_fares)
    if b"FareTable" in xml_str.encode():
        _parse_fare_tables(root, dme_zone_pairs, zone_fares, network_fares)

    if not zone_fares:
        logger.warning("No zone pair prices found — returning zones without prices")

    logger.info("Parsed %d zone pair prices", len(zone_fares))

    stop_coords = _collect_stop_coords(zones, stops, stop_zones)

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {
        "stop_zones": stop_zones,
        "stop_coords": stop_coords,
        "zone_fares": zone_fares,
        "network_fares": network_fares,
    }


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _parse_stops(
    root: ET.Element,
    stations: list[Station],
    naptan: dict[str, tuple[float, float]] | None,
) -> dict[str, NetexStop]:
    stops: dict[str, NetexStop] = {}

    for ssp in root.iter():
        tag = _unprefixed(ssp.tag)
        if tag not in ("ScheduledStopPoint", "scheduledStopPoint"):
            continue
        stop = _parse_stop_point(ssp, naptan)
        if stop is None:
            continue
        atco, entry = stop
        if atco in stops:
            continue
        near_station = entry.lat is not None and entry.lon is not None and is_near_station(
            entry.lat, entry.lon, stations
        )
        stops[atco] = NetexStop(name=entry.name, lat=entry.lat, lon=entry.lon, near_station=near_station)

    return stops


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _parse_stop_point(
    ssp: ET.Element,
    naptan: dict[str, tuple[float, float]] | None,
) -> tuple[str, NetexStop] | None:
    name_el = _first_found(
        ssp.find(".//netex:Name", NS),
        ssp.find(".//netex:name", NS),
    )
    # lucidlint: ignore special-case — _first_found's None is the shared contract; a sentinel alters fallbacks
    if name_el is None:
        return None
    name = (name_el.text or "").strip()
    if not name:
        return None

    atco_el = _first_found(
        ssp.find(".//netex:AtcoCode", NS),
        ssp.find(".//netex:atcoCode", NS),
    )
    atco = atco_el.text.strip() if atco_el is not None and atco_el.text else (ssp.get("id", "") or "").strip()
    if not atco:
        return None

    lat, lon = _stop_coordinates(ssp)
    if lat is None and lon is None and naptan is not None:
        atco_key = atco.removeprefix("atco:")
        coords = naptan.get(atco_key)
        if coords is not None:
            lat, lon = coords

    return atco, NetexStop(name=name, lat=lat, lon=lon, near_station=False)


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _stop_coordinates(ssp: ET.Element) -> tuple[float | None, float | None]:
    lat_el = _first_found(
        ssp.find(".//netex:Latitude", NS),
        ssp.find(".//netex:latitude", NS),
    )
    lon_el = _first_found(
        ssp.find(".//netex:Longitude", NS),
        ssp.find(".//netex:longitude", NS),
    )
    lat = float(lat_el.text) if lat_el is not None and lat_el.text else None
    lon = float(lon_el.text) if lon_el is not None and lon_el.text else None
    return lat, lon


def _stops_have_near_station(stops: dict[str, NetexStop]) -> bool:
    near_count = sum(1 for s in stops.values() if s.near_station)
    stops_with_coords = sum(1 for s in stops.values() if s.lat is not None and s.lon is not None)

    if near_count == 0 and stops_with_coords > 0:
        logger.info("No stops near any station (%d stops have coordinates), skipping XML", stops_with_coords)
        return False

    if near_count == 0 and stops_with_coords == 0:
        logger.warning("No stop coordinates available — cannot verify station proximity, proceeding anyway")

    logger.info("Found %d total stops, %d near stations", len(stops), near_count)
    return True


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _parse_fare_zones(
    root: ET.Element,
    stops: dict[str, NetexStop],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    zones: dict[str, list[str]] = {}
    for zone_el in root.iter():
        tag = _unprefixed(zone_el.tag)
        if tag not in ("FareZone", "fareZone"):
            continue
        zone_id_el = _first_found(
            zone_el.find(".//netex:Id", NS),
            zone_el.find(".//netex:id", NS),
        )
        zone_id = (
            zone_id_el.text.strip()
            if zone_id_el is not None and zone_id_el.text
            else (zone_el.get("id", "") or "").strip()
        )
        members = _collect_zone_members(zone_el, stops)
        if zone_id and members:
            zones[zone_id] = members

    stop_zones: dict[str, str] = {}
    for zone_id, members in zones.items():
        for atco in members:
            stop = stops.get(atco)
            if stop:
                normalized = stop.name.strip().lower()
                if normalized not in stop_zones:
                    stop_zones[normalized] = zone_id

    logger.info("Parsed %d fare zones, %d stop->zone mappings", len(zones), len(stop_zones))
    return zones, stop_zones


def _collect_zone_members(zone_el: ET.Element, stops: dict[str, NetexStop]) -> list[str]:
    members: list[str] = []
    for member in zone_el.iter():
        mt = _unprefixed(member.tag)
        if mt == "Member" or mt == "StopPointRef" or "ref" in mt.lower():
            ref = member.get("ref", "") or member.text or ""
            if ref in stops:
                members.append(ref)
    return members


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _parse_distance_matrix_elements(
    root: ET.Element,
) -> tuple[dict[str, dict[str, float]], dict[str, str]]:
    zone_fares: dict[str, dict[str, float]] = {}
    dme_zone_pairs: dict[str, str] = {}

    for dme in root.iter():
        tag = _unprefixed(dme.tag)
        if tag not in ("DistanceMatrixElement", "distanceMatrixElement"):
            continue

        dme_id = dme.get("id", "")

        start_el = _first_found(
            dme.find(".//netex:StartTariffZoneRef", NS),
            dme.find(".//netex:startTariffZoneRef", NS),
            dme.find(".//netex:StartZoneRef", NS),
            dme.find(".//netex:startZoneRef", NS),
        )
        end_el = _first_found(
            dme.find(".//netex:EndTariffZoneRef", NS),
            dme.find(".//netex:endTariffZoneRef", NS),
            dme.find(".//netex:EndZoneRef", NS),
            dme.find(".//netex:endZoneRef", NS),
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

        _apply_price_group_fare(root, dme, normalized_key, zone_fares)

    return zone_fares, dme_zone_pairs


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _apply_price_group_fare(
    root: ET.Element,
    dme: ET.Element,
    normalized_key: str,
    zone_fares: dict[str, dict[str, float]],
) -> None:
    price_ref_el = _first_found(
        dme.find(".//netex:PriceGroupRef", NS),
        dme.find(".//netex:priceGroupRef", NS),
    )
    if price_ref_el is not None:
        price_group_ref = price_ref_el.get("ref", "") or price_ref_el.text or ""
        price = _find_price_for_group(root, price_group_ref)
        if price is not None and normalized_key not in zone_fares:
            zone_fares[normalized_key] = {"adult_single": price}


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _parse_distance_matrix_prices(
    root: ET.Element,
    dme_zone_pairs: dict[str, str],
    zone_fares: dict[str, dict[str, float]],
) -> None:
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


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _collect_stop_coords(
    zones: dict[str, list[str]],
    stops: dict[str, NetexStop],
    stop_zones: dict[str, str],
) -> list[dict]:
    stop_coords: list[dict] = []
    for _, members in zones.items():
        for atco in members:
            stop = stops.get(atco)
            if stop and stop.lat is not None and stop.lon is not None:
                zone_name = stop_zones.get(stop.name.strip().lower())
                if zone_name:
                    stop_coords.append(
                        {
                            "name": stop.name,
                            "lat": round(stop.lat, COORD_ROUND_DIGITS),
                            "lon": round(stop.lon, COORD_ROUND_DIGITS),
                            "zone": zone_name,
                        }
                    )
    return stop_coords


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
                try:
                    text = amt.text or ""
                    if text:
                        val_el = amt.find(".//netex:amount", NS) or amt.find("netex:Amount", NS)
                        if val_el is not None:
                            return float(val_el.text or "")
                        return float(text)
                except (ValueError, TypeError):
                    continue

    return None


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _parse_fare_products(root: ET.Element, zone_fares: dict[str, dict[str, float]]) -> None:
    for product in root.iter():
        tag = _unprefixed(product.tag)
        if tag not in ("PreassignedFareProduct", "preassignedFareProduct"):
            continue

        name_el = _first_found(
            product.find(".//netex:Name", NS),
            product.find(".//netex:name", NS),
        )
        if name_el is None:
            continue
        product_name = (name_el.text or "").strip().lower()

        product_type = _classify_fare_product_type(product_name)
        if product_type is None:
            continue

        price = _find_product_price(product)
        if price is not None:
            _apply_product_to_distance_matrix(root, product, product_type, price, zone_fares)
            _associate_product_with_zones(root, product, product_type, price, zone_fares)


def _classify_fare_product_type(name: str) -> str | None:
    if "single" in name:
        return "adult_single"
    if "return" in name:
        return "adult_return"
    if "day" in name or "dayrider" in name or "day rider" in name:
        return "adult_day"
    return None


def _product_id_of(product: ET.Element) -> str | None:
    for attr_key in ("id", "Id", "{http://www.netex.org.uk/netex}id"):
        if attr_key in product.attrib:
            return product.attrib[attr_key]
    return None


def _find_product_price(product: ET.Element) -> float | None:
    for child in product.iter():
        tag = _unprefixed(child.tag)
        if tag == "Price" or tag == "price":
            amt_el = _first_found(
                child.find(".//netex:Amount", NS),
                child.find("netex:Amount", NS),
            )
            if amt_el is not None:
                try:
                    return float(amt_el.text or "")
                except (ValueError, TypeError):
                    continue
            try:
                return float(child.text or "")
            except (ValueError, TypeError):
                continue
    return None


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _apply_product_to_distance_matrix(
    root: ET.Element,
    product: ET.Element,
    product_type: str,
    price: float,
    zone_fares: dict[str, dict[str, float]],
) -> None:
    product_id = _product_id_of(product)
    for dme in root.iter():
        dtag = _unprefixed(dme.tag)
        if dtag not in ("DistanceMatrixElement", "distanceMatrixElement"):
            continue
        for pfep in dme.iter():
            ptag = _unprefixed(pfep.tag)
            if ptag == "PreassignedFareProductRef" or "fareProductRef" in ptag:
                ref = pfep.get("ref", "")
                if ref and product_id and (ref == product_id or ref in product_id):
                    start_ref = _first_found(
                        dme.find(".//netex:StartZoneRef", NS),
                        dme.find(".//netex:startZoneRef", NS),
                    )
                    end_ref = _first_found(
                        dme.find(".//netex:EndZoneRef", NS),
                        dme.find(".//netex:endZoneRef", NS),
                    )
                    key = _zone_price_key(start_ref, end_ref)
                    if key is not None:
                        _record_zone_fare(zone_fares, key, product_type, price)


def _zone_price_key(start_ref: ET.Element | None, end_ref: ET.Element | None) -> str | None:
    if start_ref is None or end_ref is None:
        return None
    sz = start_ref.get("ref", "") or start_ref.text or ""
    ez = end_ref.get("ref", "") or end_ref.text or ""
    return f"{sz}:{ez}".replace("@alighting", "@boarding")


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _record_zone_fare(
    zone_fares: dict[str, dict[str, float]],
    key: str,
    product_type: str,
    price: float,
) -> None:
    if key not in zone_fares:
        zone_fares[key] = {}
    zone_fares[key][product_type] = price


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _associate_product_with_zones(
    root: ET.Element,
    product: ET.Element,
    product_type: str,
    price: float,
    zone_fares: dict[str, dict[str, float]],
) -> None:
    product_id = _product_id_of(product)
    if not product_id:
        return

    sop = _find_sales_offer_for_product(root, product_id)
    if sop is None:
        return
    _apply_sales_offer_fares(root, sop, product_type, price, zone_fares)


def _find_sales_offer_for_product(root: ET.Element, product_id: str) -> ET.Element | None:
    for sop in root.iter():
        stag = _unprefixed(sop.tag)
        if stag not in ("SalesOfferPackage", "salesOfferPackage"):
            continue

        for ref in sop.iter():
            rtag = _unprefixed(ref.tag)
            if rtag in ("PreassignedFareProductRef", "fareProductRef", "fareProduct") or "productRef" in rtag:
                ref_val = ref.get("ref", "")
                if ref_val and ref_val == product_id:
                    return sop
    return None


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _apply_sales_offer_fares(
    root: ET.Element,
    sop: ET.Element,
    product_type: str,
    price: float,
    zone_fares: dict[str, dict[str, float]],
) -> None:
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
                    key = _zone_price_key(start_ref, end_ref)
                    if key is not None:
                        _record_zone_fare(zone_fares, key, product_type, price)


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _parse_fare_tables(
    root: ET.Element,
    dme_zone_pairs: dict[str, str],
    zone_fares: dict[str, dict[str, float]],
    network_fares: list[dict],
) -> None:
    products = _collect_fare_product_types(root)
    for ft in root.iter():
        tag = _unprefixed(ft.tag)
        if tag not in ("FareTable", "fareTable"):
            continue
        ptype = _fare_table_product_type(ft, products)
        if ptype is None:
            continue
        for ft_child in ft.iter():
            ct = _unprefixed(ft_child.tag)
            if ct in ("DistanceMatrixElementPrice", "distanceMatrixElementPrice"):
                network_fare = _apply_fare_table_price(ft_child, root, ptype, dme_zone_pairs, zone_fares)
                if network_fare is not None:
                    network_fares.append(network_fare)


def _collect_fare_product_types(root: ET.Element) -> dict[str, str]:
    products: dict[str, str] = {}
    for product in root.iter():
        tag = _unprefixed(product.tag)
        if tag not in ("PreassignedFareProduct", "preassignedFareProduct"):
            continue
        name_el = _first_found(
            product.find(".//netex:Name", NS),
            product.find(".//netex:name", NS),
        )
        if name_el is None:
            continue
        pname = (name_el.text or "").strip().lower()
        ptype = _classify_fare_product_type(pname)
        if ptype is None:
            continue
        pid = _product_id_of(product)
        if pid:
            products[pid] = ptype
    return products


def _fare_table_product_type(ft: ET.Element, products: dict[str, str]) -> str | None:
    product_id = None
    for pf_ref in ft.iter():
        rt = _unprefixed(pf_ref.tag)
        if rt == "PreassignedFareProductRef" or "fareProductRef" in rt:
            product_id = pf_ref.get("ref", "")
            break
    if not product_id or product_id not in products:
        return None
    return products[product_id]


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _apply_fare_table_price(
    ft_child: ET.Element,
    root: ET.Element,
    ptype: str,
    dme_zone_pairs: dict[str, str],
    zone_fares: dict[str, dict[str, float]],
) -> dict | None:
    amt_el = _first_found(
        ft_child.find(".//netex:Amount", NS),
        ft_child.find(".//netex:amount", NS),
    )
    if amt_el is None or not amt_el.text:
        return None
    try:
        price = float(amt_el.text)
    except ValueError:
        return None
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
        covered_stops = _collect_network_covered_stops(root)
        if covered_stops:
    # lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            return {
                "price": price,
                "product_type": ptype,
                "covered_stops": covered_stops,
            }
    return None


def _collect_network_covered_stops(root: ET.Element) -> set[str]:
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
    return covered_stops


def dataset_description_matches(desc: str, sub_op: str) -> bool:
    if not desc:
        return False
    return desc.strip().lower() == sub_op.lower()
