"""NeTEx XML parsing and bus fare zone extraction."""

from __future__ import annotations

import csv
import logging
import math
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import field_serializer

from serialization import SerializableAsDict

logger = logging.getLogger(__name__)
STATIONS_CSV = Path("data/stations.csv")
NAPTAN_CACHE = Path("data/bods_stops.csv")

type _StationGrid = list[list[list[Station]]]
type _ParsedStop = tuple[str, NetexStop]
type _ZoneParse = tuple[dict[str, list[str]], dict[str, str]]
type _DmeParse = tuple[ZoneFares, dict[str, str]]

# ── the parser's output records ──────────────────────────────────────
# Each record's dict IS its wire form: the model declares the key order and
# the nested records, and ``to_dict`` comes from the boundary base.

class ParseResult(SerializableAsDict):
    """The parser's output record for one NeTEx fares file."""

    stop_zones: dict[str, str]
    stop_coords: list[StopCoord]
    zone_fares: dict[str, dict[str, float]]
    network_fares: list[NetworkFare]


class ZoneFares(dict[str, dict[str, float]]):
    """The zone-pair fare table: pair key → {fare product → price}.

    The key states the zone pair and the boarding rule (``"Z1:Z2@boarding"``);
    the inner map holds one price per fare product. A keyed collection that
    owns its two writes, so no parser section builds the inner map by hand.
    """

    def put(self, pair: str, product: str, price: float) -> None:
        """Set one fare product's price for a zone pair."""
        self.setdefault(pair, {})[product] = price

    def put_if_absent(self, pair: str, product: str, price: float) -> None:
        """Set the price only when the pair has none for this product yet —
        the first statement of a fare wins; later sections restate it."""
        self.setdefault(pair, {}).setdefault(product, price)

def _first_found(*elements):
    for el in elements:
        if el is not None:
            return el
    return None


def _unprefixed(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def xml_bytes(el: ET.Element) -> bytes:
    """The element serialized back to bytes — used for cheap marker scans."""
    return ET.tostring(el, encoding="unicode").encode()


@dataclass
class Station:
    name: str
    crs: str
    lat: float
    long: float


class NetexStop(SerializableAsDict):
    """A NeTEx ScheduledStopPoint record: name, coordinates (when present),
    and whether it lies near a railway station."""

    name: str
    lat: float | None
    lon: float | None
    near_station: bool


class StopCoord(SerializableAsDict):
    """A zone member's map entry: stop name, rounded coordinates, zone id."""

    name: str
    lat: float
    lon: float
    zone: str


class NetworkFare(SerializableAsDict):
    """A network-wide fare (day/return) that is not scoped to a zone pair."""

    price: float
    product_type: str
    covered_stops: set[str]

    @field_serializer("covered_stops")
    @staticmethod
    def _serialize_covered_stops(stops: set[str]) -> list[str]:
        """The wire shape is a sorted list — a set is not JSON."""
        return sorted(stops)


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


def _naptan_from_rows(rows):
    """Parse NaPTAN CSV rows (file or text lines) into {atco: (lat, lon)}."""
    naptan: dict[str, tuple[float, float]] = {}
    for row in csv.DictReader(rows):
        atco = row.get("ATCOCode", "").strip()
        lat_raw = row.get("Latitude", "").strip()
        lon_raw = row.get("Longitude", "").strip()
        if atco and lat_raw and lon_raw:
            try:
                naptan[atco] = (float(lat_raw), float(lon_raw))
            except ValueError:
                continue
    return naptan


def load_naptan_stops() -> Mapping[str, tuple[float, float]] | None:
    naptan: dict[str, tuple[float, float]] = {}

    if NAPTAN_CACHE.is_file():
        logger.info("Loading NaPTAN stops from %s", NAPTAN_CACHE)
        with NAPTAN_CACHE.open(newline="") as f:
            naptan = _naptan_from_rows(f)
        logger.info("Loaded %d NaPTAN stop coordinates", len(naptan))
        return naptan

    logger.info("Downloading NaPTAN stop data from %s (101MB)", _NetexFares.NAPTAN_DOWNLOAD)
    try:
        resp = httpx.get(_NetexFares.NAPTAN_DOWNLOAD, timeout=300, follow_redirects=True)
        resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException) as e:
        logger.warning("Failed to download NaPTAN data: %s", e)
        return None

    NAPTAN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with NAPTAN_CACHE.open("wb") as f:
        f.write(resp.content)

    naptan = _naptan_from_rows(resp.text.splitlines())
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




def _classify_fare_product_type(name: str) -> str | None:
    if "single" in name:
        return "adult_single"
    if "return" in name:
        return "adult_return"
    if "day" in name or "dayrider" in name or "day rider" in name:
        return "adult_day"
    return None


def _as_float(text: str | None) -> float | None:
    try:
        return float(text or "")
    except (ValueError, TypeError):
        return None


def parse_netex_fares(
    xml_str: str,
    stations: list[Station],
    naptan: Mapping[str, tuple[float, float]] | None = None,
) -> ParseResult | None:
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        logger.warning("XML parse error: %s", e)
        return None
    return _NetexFares(root, stations, naptan).run()


# field-disjoint partition exists to split along; size is a review signal, not a split order (review-log)
@dataclass(frozen=True)
class _StopCoordinates:
    """The parsed coordinates of a ScheduledStopPoint (either may be absent)."""

    lat: float | None
    lon: float | None

    def with_fallback(self, naptan: Mapping[str, tuple[float, float]], atco: str) -> _StopCoordinates:
        """NaPTAN lookup fills both coordinates when the XML carried none."""
        if self.lat is None and self.lon is None:
            coords = naptan.get(atco.removeprefix("atco:"))
            if coords is not None:
                return _StopCoordinates(lat=coords[0], lon=coords[1])
        return self

# lucidlint: ignore latent-class one parse surface — 27 methods and no field-disjoint partition (review-log, #47)
class _NetexFares:
    """The fares of one BODS NeTEx file: stops, zone pairs and products.

    The section parsers are methods cooperating through this state (stops,
    zone_fares, dme_zone_pairs, network_fares) and produce the output record
    set on :meth:`run`. Private: the module's surface is ``parse_netex_fares``
    and the records it returns.
    """

    NS = {
        "netex": "http://www.netex.org.uk/netex",
        "fxc": "http://www.netex.org.uk/fxc",
    }
    NAPTAN_DOWNLOAD = "https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=csv"
    GRID_COLS = 36
    GRID_ROWS = 18
    GRID_CELL_DEG = 0.5
    # lucidlint: ignore magic-number geographic bounding-box origin — west edge, Longitude −7.5 (data verdict)
    GRID_LON_ORIGIN = -7.5
    GRID_LAT_ORIGIN = 49.5
    COORD_ROUND_DIGITS = 5

    def __init__(
        self,
        root: ET.Element,
        stations: list[Station],
        naptan: Mapping[str, tuple[float, float]] | None = None,
    ):
        self.root: ET.Element = root
        self.stations: list[Station] = stations
        self.naptan: Mapping[str, tuple[float, float]] | None = naptan
        self.stops: dict[str, NetexStop] = {}
        self.zone_fares: ZoneFares = ZoneFares()
        self.dme_zone_pairs: dict[str, str] = {}
        self.network_fares: list[NetworkFare] = []
        self._station_grid: _StationGrid | None = None

    def _build_station_grid(self) -> _StationGrid:
        grid: list[list[list[Station]]] = [
            [[] for _ in range(self.GRID_COLS)] for _ in range(self.GRID_ROWS)
        ]
        for s in self.stations:
            col = int((s.long - self.GRID_LON_ORIGIN) / self.GRID_CELL_DEG)
            row = int((s.lat - self.GRID_LAT_ORIGIN) / self.GRID_CELL_DEG)
            if 0 <= col < self.GRID_COLS and 0 <= row < self.GRID_ROWS:
                grid[row][col].append(s)
        logger.info("Built station grid (%d×%d cells)", len(grid), len(grid[0]))
        return grid

    def _is_near_station(self, lat: float, lon: float, max_dist_km: float = 0.2) -> bool:
        if self._station_grid is None:
            self._station_grid = self._build_station_grid()
        grid = self._station_grid
        col = int((lon - self.GRID_LON_ORIGIN) / self.GRID_CELL_DEG)
        row = int((lat - self.GRID_LAT_ORIGIN) / self.GRID_CELL_DEG)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                r, c = row + dr, col + dc
                if 0 <= r < self.GRID_ROWS and 0 <= c < self.GRID_COLS:
                    for s in grid[r][c]:
                        if haversine_km(lat, lon, s.lat, s.long) <= max_dist_km:
                            return True
        return False


    def run(self) -> ParseResult | None:
        self._parse_stops()
        if not self.stops:
            logger.warning("No stops found in NeTEx data")
            return None
        if not self._stops_have_near_station():
            return None

        zones, stop_zones = self._parse_fare_zones()
        self.zone_fares, self.dme_zone_pairs = self._parse_distance_matrix_elements()

        self._parse_distance_matrix_prices()

        marker_bytes = xml_bytes(self.root)
        if b"PreassignedFareProduct" in marker_bytes:
            self._parse_fare_products()
        if b"FareTable" in marker_bytes:
            self._parse_fare_tables()

        if not self.zone_fares:
            logger.warning("No zone pair prices found — returning zones without prices")

        logger.info("Parsed %d zone pair prices", len(self.zone_fares))

        stop_coords = self._collect_stop_coords(zones, stop_zones)

        return ParseResult(
            stop_zones=stop_zones,
            stop_coords=stop_coords,
            zone_fares=self.zone_fares,
            network_fares=self.network_fares,
        )

    def _parse_stops(self) -> None:
        for ssp in self.root.iter():
            tag = _unprefixed(ssp.tag)
            if tag not in ("ScheduledStopPoint", "scheduledStopPoint"):
                continue
            stop = self._parse_stop_point(ssp)
            if stop is None:
                continue
            atco, entry = stop
            if atco in self.stops:
                continue
            near_station = entry.lat is not None and entry.lon is not None and self._is_near_station(
                entry.lat, entry.lon
            )
            self.stops[atco] = NetexStop(name=entry.name, lat=entry.lat, lon=entry.lon, near_station=near_station)

    def _parse_stop_point(self, ssp: ET.Element) -> _ParsedStop | None:
        name_el = _first_found(
            ssp.find(".//netex:Name", self.NS),
            ssp.find(".//netex:name", self.NS),
        )
        # lucidlint: ignore special-case — _first_found's None is the shared contract; a sentinel alters fallbacks
        if name_el is None:
            return None
        name = (name_el.text or "").strip()
        if not name:
            return None

        atco_el = _first_found(
            ssp.find(".//netex:AtcoCode", self.NS),
            ssp.find(".//netex:atcoCode", self.NS),
        )
        atco = atco_el.text.strip() if atco_el is not None and atco_el.text else (ssp.get("id", "") or "").strip()
        if not atco:
            return None

        coords = self._stop_coordinates(ssp).with_fallback(self.naptan or {}, atco)
        lat, lon = coords.lat, coords.lon

        return atco, NetexStop(name=name, lat=lat, lon=lon, near_station=False)

    @staticmethod
    def _stop_coordinates(ssp: ET.Element) -> _StopCoordinates:
        lat_el = _first_found(
            ssp.find(".//netex:Latitude", _NetexFares.NS),
            ssp.find(".//netex:latitude", _NetexFares.NS),
        )
        lon_el = _first_found(
            ssp.find(".//netex:Longitude", _NetexFares.NS),
            ssp.find(".//netex:longitude", _NetexFares.NS),
        )
        return _StopCoordinates(
            lat=float(lat_el.text) if lat_el is not None and lat_el.text else None,
            lon=float(lon_el.text) if lon_el is not None and lon_el.text else None,
        )

    def _stops_have_near_station(self) -> bool:
        near_count = sum(1 for s in self.stops.values() if s.near_station)
        stops_with_coords = sum(1 for s in self.stops.values() if s.lat is not None and s.lon is not None)

        if near_count == 0 and stops_with_coords > 0:
            logger.info("No stops near any station (%d stops have coordinates), skipping XML", stops_with_coords)
            return False

        if near_count == 0 and stops_with_coords == 0:
            logger.warning("No stop coordinates available — cannot verify station proximity, proceeding anyway")

        logger.info("Found %d total stops, %d near stations", len(self.stops), near_count)
        return True

    def _parse_fare_zones(self) -> _ZoneParse:
        zones: dict[str, list[str]] = {}
        for zone_el in self.root.iter():
            tag = _unprefixed(zone_el.tag)
            if tag not in ("FareZone", "fareZone"):
                continue
            zone_id_el = _first_found(
                zone_el.find(".//netex:Id", self.NS),
                zone_el.find(".//netex:id", self.NS),
            )
            zone_id = (
                zone_id_el.text.strip()
                if zone_id_el is not None and zone_id_el.text
                else (zone_el.get("id", "") or "").strip()
            )
            members = self._collect_zone_members(zone_el)
            if zone_id and members:
                zones[zone_id] = members

        stop_zones: dict[str, str] = {}
        for zone_id, members in zones.items():
            for atco in members:
                stop = self.stops.get(atco)
                if stop:
                    normalized = stop.name.strip().lower()
                    if normalized not in stop_zones:
                        stop_zones[normalized] = zone_id

        logger.info("Parsed %d fare zones, %d stop->zone mappings", len(zones), len(stop_zones))
        return zones, stop_zones

    def _collect_zone_members(self, zone_el: ET.Element) -> list[str]:
        members: list[str] = []
        for member in zone_el.iter():
            mt = _unprefixed(member.tag)
            if mt == "Member" or mt == "StopPointRef" or "ref" in mt.lower():
                ref = member.get("ref", "") or member.text or ""
                if ref in self.stops:
                    members.append(ref)
        return members

    def _parse_distance_matrix_elements(self) -> _DmeParse:
        for dme in self.root.iter():
            tag = _unprefixed(dme.tag)
            if tag not in ("DistanceMatrixElement", "distanceMatrixElement"):
                continue

            dme_id = dme.get("id", "")

            start_el = _first_found(
                dme.find(".//netex:StartTariffZoneRef", self.NS),
                dme.find(".//netex:startTariffZoneRef", self.NS),
                dme.find(".//netex:StartZoneRef", self.NS),
                dme.find(".//netex:startZoneRef", self.NS),
            )
            end_el = _first_found(
                dme.find(".//netex:EndTariffZoneRef", self.NS),
                dme.find(".//netex:endTariffZoneRef", self.NS),
                dme.find(".//netex:EndZoneRef", self.NS),
                dme.find(".//netex:endZoneRef", self.NS),
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
                self.dme_zone_pairs[dme_id] = key

            self._apply_price_group_fare(dme, normalized_key)

        return self.zone_fares, self.dme_zone_pairs

    def _apply_price_group_fare(self, dme: ET.Element, normalized_key: str) -> None:
        price_ref_el = _first_found(
            dme.find(".//netex:PriceGroupRef", self.NS),
            dme.find(".//netex:priceGroupRef", self.NS),
        )
        if price_ref_el is not None:
            price_group_ref = price_ref_el.get("ref", "") or price_ref_el.text or ""
            price = self._find_price_for_group(price_group_ref)
            if price is not None:
                self.zone_fares.put_if_absent(normalized_key, "adult_single", price)

    def _parse_distance_matrix_prices(self) -> None:
        for dmep in self.root.iter():
            tag = _unprefixed(dmep.tag)
            if tag not in ("DistanceMatrixElementPrice", "distanceMatrixElementPrice"):
                continue

            amount_el = dmep.find(".//netex:Amount", self.NS)
            if amount_el is None or not amount_el.text:
                continue
            try:
                price = float(amount_el.text)
            except ValueError:
                continue

            dme_ref_el = _first_found(
                dmep.find(".//netex:DistanceMatrixElementRef", self.NS),
                dmep.find(".//netex:distanceMatrixElementRef", self.NS),
            )
            if dme_ref_el is None:
                continue
            dme_ref = dme_ref_el.get("ref", "") or dme_ref_el.text or ""
            if not dme_ref:
                continue

            zone_key = self.dme_zone_pairs.get(dme_ref)
            if not zone_key:
                continue
            nk = zone_key.replace("@alighting", "@boarding")

            self.zone_fares.put_if_absent(nk, "adult_single", price)

    def _collect_stop_coords(
        self, zones: Mapping[str, list[str]], stop_zones: Mapping[str, str]
    ) -> list[StopCoord]:
        stop_coords: list[StopCoord] = []
        for _, members in zones.items():
            for atco in members:
                stop = self.stops.get(atco)
                if stop and stop.lat is not None and stop.lon is not None:
                    zone_name = stop_zones.get(stop.name.strip().lower())
                    if zone_name:
                        stop_coords.append(
                            StopCoord(
                                name=stop.name,
                                lat=round(stop.lat, self.COORD_ROUND_DIGITS),
                                lon=round(stop.lon, self.COORD_ROUND_DIGITS),
                                zone=zone_name,
                            )
                        )
        return stop_coords

    def _find_price_for_group(self, group_ref: str) -> float | None:
        for pg in self.root.iter():
            tag = _unprefixed(pg.tag)
            if tag not in ("PriceGroup", "priceGroup"):
                continue
            pg_id_el = pg.find(".//netex:id", self.NS)
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
                            val_el = amt.find(".//netex:amount", self.NS) or amt.find("netex:Amount", self.NS)
                            if val_el is not None:
                                return float(val_el.text or "")
                            return float(text)
                    except (ValueError, TypeError):
                        continue

        return None

    def _parse_fare_products(self) -> None:
        for product in self.root.iter():
            tag = _unprefixed(product.tag)
            if tag not in ("PreassignedFareProduct", "preassignedFareProduct"):
                continue

            name_el = _first_found(
                product.find(".//netex:Name", self.NS),
                product.find(".//netex:name", self.NS),
            )
            if name_el is None:
                continue
            product_name = (name_el.text or "").strip().lower()

            product_type = _classify_fare_product_type(product_name)
            if product_type is None:
                continue

            price = self._find_product_price(product)
            if price is not None:
                self._apply_product_to_distance_matrix(product, product_type, price)
                self._associate_product_with_zones(product, product_type, price)

    @staticmethod
    def _product_id_of(product: ET.Element) -> str | None:
        for attr_key in ("id", "Id", "{http://www.netex.org.uk/netex}id"):
            if attr_key in product.attrib:
                return product.attrib[attr_key]
        return None

    @staticmethod
    def _find_product_price(product: ET.Element) -> float | None:
        for child in product.iter():
            tag = _unprefixed(child.tag)
            if tag == "Price" or tag == "price":
                amt_el = _first_found(
                    child.find(".//netex:Amount", _NetexFares.NS),
                    child.find("netex:Amount", _NetexFares.NS),
                )
                # one fallback expression: the Amount element's own text, else the Price element's
                price = _as_float((amt_el if amt_el is not None else child).text)
                if price is not None:
                    return price
        return None

    def _apply_product_to_distance_matrix(self, product: ET.Element, product_type: str, price: float) -> None:
        product_id = self._product_id_of(product)
        for dme in self.root.iter():
            dtag = _unprefixed(dme.tag)
            if dtag not in ("DistanceMatrixElement", "distanceMatrixElement"):
                continue
            for pfep in dme.iter():
                ptag = _unprefixed(pfep.tag)
                if ptag == "PreassignedFareProductRef" or "fareProductRef" in ptag:
                    ref = pfep.get("ref", "")
                    if ref and product_id and (ref == product_id or ref in product_id):
                        start_ref = _first_found(
                            dme.find(".//netex:StartZoneRef", self.NS),
                            dme.find(".//netex:startZoneRef", self.NS),
                        )
                        end_ref = _first_found(
                            dme.find(".//netex:EndZoneRef", self.NS),
                            dme.find(".//netex:endZoneRef", self.NS),
                        )
                        key = self._zone_price_key(start_ref, end_ref)
                        if key is not None:
                            self._record_zone_fare(key, product_type, price)

    @staticmethod
    def _zone_price_key(start_ref: ET.Element | None, end_ref: ET.Element | None) -> str | None:
        if start_ref is None or end_ref is None:
            return None
        sz = start_ref.get("ref", "") or start_ref.text or ""
        ez = end_ref.get("ref", "") or end_ref.text or ""
        return f"{sz}:{ez}".replace("@alighting", "@boarding")

    def _record_zone_fare(self, key: str, product_type: str, price: float) -> None:
        self.zone_fares.put(key, product_type, price)

    def _associate_product_with_zones(self, product: ET.Element, product_type: str, price: float) -> None:
        product_id = self._product_id_of(product)
        if not product_id:
            return

        sop = self._find_sales_offer_for_product(product_id)
        if sop is None:
            return
        self._apply_sales_offer_fares(sop, product_type, price)

    def _find_sales_offer_for_product(self, product_id: str) -> ET.Element | None:
        for sop in self.root.iter():
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

    def _apply_sales_offer_fares(self, sop: ET.Element, product_type: str, price: float) -> None:
        for dme in self.root.iter():
            dtag = _unprefixed(dme.tag)
            if dtag not in ("DistanceMatrixElement", "distanceMatrixElement"):
                continue
            for dme_sop_ref in dme.iter():
                ds_tag = _unprefixed(dme_sop_ref.tag)
                if ds_tag in ("SalesOfferPackageRef", "sopRef"):
                    sop_ref = dme_sop_ref.get("ref", "")
                    sop_id = sop.get("id", sop.attrib.get("{http://www.netex.org.uk/netex}id", ""))
                    if sop_ref and sop_id and sop_ref == sop_id:
                        start_ref = dme.find(
                            ".//netex:StartZoneRef", self.NS
                        ) or dme.find(".//netex:startZoneRef", self.NS)
                        end_ref = dme.find(
                            ".//netex:EndZoneRef", self.NS
                        ) or dme.find(".//netex:endZoneRef", self.NS)
                        key = self._zone_price_key(start_ref, end_ref)
                        if key is not None:
                            self._record_zone_fare(key, product_type, price)

    def _parse_fare_tables(self) -> None:
        products = self._collect_fare_product_types()
        for ft in self.root.iter():
            tag = _unprefixed(ft.tag)
            if tag not in ("FareTable", "fareTable"):
                continue
            ptype = self._fare_table_product_type(ft, products)
            if ptype is None:
                continue
            for ft_child in ft.iter():
                ct = _unprefixed(ft_child.tag)
                if ct in ("DistanceMatrixElementPrice", "distanceMatrixElementPrice"):
                    network_fare = self._apply_fare_table_price(ft_child, ptype)
                    if network_fare is not None:
                        self.network_fares.append(network_fare)

    def _collect_fare_product_types(self) -> dict[str, str]:
        products: dict[str, str] = {}
        for product in self.root.iter():
            tag = _unprefixed(product.tag)
            if tag not in ("PreassignedFareProduct", "preassignedFareProduct"):
                continue
            name_el = _first_found(
                product.find(".//netex:Name", self.NS),
                product.find(".//netex:name", self.NS),
            )
            if name_el is None:
                continue
            pname = (name_el.text or "").strip().lower()
            ptype = _classify_fare_product_type(pname)
            if ptype is None:
                continue
            pid = self._product_id_of(product)
            if pid:
                products[pid] = ptype
        return products

    @staticmethod
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

    def _apply_fare_table_price(self, ft_child: ET.Element, ptype: str) -> NetworkFare | None:
        amt_el = _first_found(
            ft_child.find(".//netex:Amount", self.NS),
            ft_child.find(".//netex:amount", self.NS),
        )
        if amt_el is None or not amt_el.text:
            return None
        try:
            price = float(amt_el.text)
        except ValueError:
            return None
        dme_ref_el = _first_found(
            ft_child.find(".//netex:DistanceMatrixElementRef", self.NS),
            ft_child.find(".//netex:distanceMatrixElementRef", self.NS),
        )
        if dme_ref_el is not None:
            dme_ref = dme_ref_el.get("ref", "") or dme_ref_el.text or ""
            zone_key = self.dme_zone_pairs.get(dme_ref)
            if zone_key:
                nk = zone_key.replace("@alighting", "@boarding")
                self.zone_fares.put(nk, ptype, price)
        elif ptype in ("adult_day", "adult_return"):
            covered_stops = self._collect_network_covered_stops()
            if covered_stops:
                return NetworkFare(price=price, product_type=ptype, covered_stops=covered_stops)
        return None

    def _collect_network_covered_stops(self) -> set[str]:
        covered_stops: set[str] = set()
        for t_el in self.root.iter():
            tt = _unprefixed(t_el.tag)
            if tt == "Tariff":
                for fz_ref in t_el.iter():
                    zt = _unprefixed(fz_ref.tag)
                    if zt == "FareZoneRef":
                        zone_id = fz_ref.get("ref", "")
                        if zone_id:
                            for fz in self.root.iter():
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
