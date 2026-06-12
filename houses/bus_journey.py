"""Bus journey fare products — stop-zone matching and priced fare products.

Data is loaded lazily from ``data/bus_fares.json``, which contains operator
zone maps, zone-pair pricing, and stop coordinates.

The registry exposes available fare products (SINGLE, RETURN, DAY) for a
given origin/destination stop pair.  Coordinate-based zone matching uses a
fixed 100m radius — no expanding-radius search (the point of taking the bus
is to avoid long walks).

A convenience function, ``cheapest_round_trip``, implements the "weekday
peak return" selection.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

from money import Money

from houses.geo import GeoPoint

logger = logging.getLogger(__name__)

_BUS_FARES_PATH = Path("data/bus_fares.json")

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


class FareProductType(Enum):
    """The type of bus fare product available for a zone pair."""

    SINGLE = auto()  # adult_single in the JSON
    RETURN = auto()  # adult_return
    DAY = auto()  # adult_day


# Maps JSON product keys to FareProductType
_KEY_TO_TYPE: dict[str, FareProductType] = {
    "adult_single": FareProductType.SINGLE,
    "adult_return": FareProductType.RETURN,
    "adult_day": FareProductType.DAY,
}


@dataclass(frozen=True)
class FareProduct:
    """A specific priced fare product between two zones.

    A zone pair has at most one product of each ``FareProductType``
    (asserted at load time).
    """

    type: FareProductType
    price: Money  # always GBP
    operator: str
    zone_pair: str


@dataclass
class BusJourney:
    """A bus journey between two stops, with available fare options.

    ``available_fares`` is keyed by product type — at most one entry per key.
    """

    origin: GeoPoint | None = None
    destination: GeoPoint | None = None
    duration_minutes: int | None = None
    available_fares: dict[FareProductType, FareProduct] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def _norm_tokens(s: str) -> set[str]:
    """Normalise a stop name into a set of tokens for fuzzy matching."""
    core = s.split(", ", 1)[-1]
    no_punct = re.sub(r"[.,;:'\"!?()]", "", core)
    return set(no_punct.split())


def _zone_pair_key(dep_zone: str, arr_zone: str) -> str:
    """Canonical zone pair key (dep:arr)."""
    return f"{dep_zone}:{arr_zone}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class BusJourneyRegistry:
    """Lazy-loaded registry of bus fare zone data.

    Loads ``data/bus_fares.json`` on first query.  Provides zone-pair
    fare products for a given origin/destination stop pair, using a
    multi-stage lookup: direct name → fuzzy token match → coordinate.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] | None = None
        self._meta: dict[str, Any] | None = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._loaded:
            return
        if not _BUS_FARES_PATH.is_file():
            logger.warning("Bus fares file not found at %s", _BUS_FARES_PATH)
            self._data = {}
            self._meta = {}
            self._loaded = True
            return
        with _BUS_FARES_PATH.open() as f:
            raw: dict = json.load(f)

        self._meta = raw.pop("_meta", {})

        # Validate data integrity
        self._assert_no_duplicate_products(raw)

        self._data = raw
        self._loaded = True

    @staticmethod
    def _assert_no_duplicate_products(data: dict[str, Any]) -> None:
        """Fail fast if any zone pair has multiple products of the same type.

        The ``dict[FareProductType, FareProduct]`` contract depends on this.
        """
        for op_key, op_data in data.items():
            if op_key == "_meta":
                continue
            zone_fares = op_data.get("zone_fares", {})
            for zp, products in zone_fares.items():
                seen: set[str] = set()
                for key in products:
                    if key.startswith("adult_"):
                        if key in seen:
                            raise ValueError(
                                f"Duplicate fare product '{key}' for zone pair '{zp}' in operator '{op_key}'"
                            )
                        seen.add(key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def national_max_single(self) -> Money | None:
        """Government fare cap applied to single fares (from ``_meta``)."""
        self._load()
        if self._meta is None:
            return None
        val = self._meta.get("national_max_single_gbp")
        if val is not None:
            return Money(str(val), "GBP")
        return None

    def fares_for_stops(
        self,
        dep_stop_name: str,
        arr_stop_name: str,
        dep_point: dict[str, float] | None = None,
        arr_point: dict[str, float] | None = None,
    ) -> dict[FareProductType, FareProduct]:
        """Look up available fare products between two bus stops.

        Multi-stage lookup:
        1. Direct name match against ``stop_zones``
        2. Token-set fuzzy match (≥85% token overlap)
        3. Coordinate-based match (within radius)

        Returns a dict with at most 3 keys (SINGLE, RETURN, DAY), or an
        empty dict if no zone pair is found.
        """
        self._load()
        if not self._data:
            return {}

        dep_norm = dep_stop_name.strip().lower()
        arr_norm = arr_stop_name.strip().lower()

        # Alternative name: take the part after the first ", "
        dep_alt = dep_norm.split(", ", 1)[-1] if ", " in dep_norm else dep_norm
        arr_alt = arr_norm.split(", ", 1)[-1] if ", " in arr_norm else arr_norm

        # ── Stage 1: Direct name match ──────────────────────────────
        result = self._match_zone_pair(dep_norm, dep_alt, arr_norm, arr_alt)
        if result:
            return result

        # ── Stage 2: Fuzzy token match ──────────────────────────────
        result = self._fuzzy_match(dep_norm, arr_norm)
        if result:
            return result

        # ── Stage 3: Coordinate fallback ────────────────────────────
        if dep_point is not None and arr_point is not None:
            result = self._coord_match(
                dep_stop_name,
                arr_stop_name,
                dep_point["lat"],
                dep_point["lon"],
                arr_point["lat"],
                arr_point["lon"],
            )
            if result:
                return result

        logger.warning(
            "Bus fare zone pair not found for '%s' → '%s'",
            dep_stop_name,
            arr_stop_name,
        )
        return {}

    # ------------------------------------------------------------------
    # Internal matching stages
    # ------------------------------------------------------------------

    def _match_zone_pair(
        self,
        dep_norm: str,
        dep_alt: str,
        arr_norm: str,
        arr_alt: str,
    ) -> dict[FareProductType, FareProduct]:
        """Try direct name match across all operators."""
        for op_key, op_data in self._data.items():
            if op_key == "_meta":
                continue
            stop_zones: dict[str, str] = op_data.get("stop_zones", {})
            dep_zone = stop_zones.get(dep_norm) or stop_zones.get(dep_alt)
            arr_zone = stop_zones.get(arr_norm) or stop_zones.get(arr_alt)
            if dep_zone and arr_zone:
                products = self._products_for_zone_pair(op_key, dep_zone, arr_zone)
                if products:
                    return products
        return {}

    def _fuzzy_match(
        self,
        dep_norm: str,
        arr_norm: str,
    ) -> dict[FareProductType, FareProduct]:
        """Token-set fuzzy match (≥85% Jaccard similarity)."""
        dep_tokens = _norm_tokens(dep_norm)
        arr_tokens = _norm_tokens(arr_norm)

        if not dep_tokens or not arr_tokens:
            return {}

        dep_zone: str | None = None
        arr_zone: str | None = None

        for op_key, op_data in self._data.items():
            if op_key == "_meta":
                continue
            stop_zones: dict[str, str] = op_data.get("stop_zones", {})
            if dep_zone is None:
                for bods_name, zone in stop_zones.items():
                    bods_tokens = _norm_tokens(bods_name)
                    inter = dep_tokens & bods_tokens
                    union = dep_tokens | bods_tokens
                    if union and len(inter) / len(union) >= 0.85:
                        dep_zone = zone
                        logger.warning(
                            "Bus fare fuzzy match dep='%s' -> '%s' zone=%s",
                            dep_norm,
                            bods_name,
                            dep_zone,
                        )
                        if arr_zone is not None:
                            break
            if arr_zone is None:
                for bods_name, zone in stop_zones.items():
                    bods_tokens = _norm_tokens(bods_name)
                    inter = arr_tokens & bods_tokens
                    union = arr_tokens | bods_tokens
                    if union and len(inter) / len(union) >= 0.85:
                        arr_zone = zone
                        logger.warning(
                            "Bus fare fuzzy match arr='%s' -> '%s' zone=%s",
                            arr_norm,
                            bods_name,
                            arr_zone,
                        )
                        if dep_zone is not None:
                            break
            if dep_zone and arr_zone:
                products = self._products_for_zone_pair(op_key, dep_zone, arr_zone)
                if products:
                    return products
        return {}

    _COORD_RADIUS_KM: float = 0.1  # 100m — point of taking bus is to avoid long walks

    def _coord_match(
        self,
        dep_stop_name: str,
        arr_stop_name: str,
        dep_lat: float,
        dep_lon: float,
        arr_lat: float,
        arr_lon: float,
    ) -> dict[FareProductType, FareProduct]:
        """Coordinate-based zone matching (100m radius)."""
        for op_key in self._data:
            if op_key == "_meta":
                continue
            stop_coords: list[dict] = self._data[op_key].get("stop_coords", [])
            if not stop_coords:
                continue
            dep_zone = self._nearest_zone(dep_lat, dep_lon, stop_coords, radius_km=self._COORD_RADIUS_KM)
            arr_zone = self._nearest_zone(arr_lat, arr_lon, stop_coords, radius_km=self._COORD_RADIUS_KM)
            if dep_zone and arr_zone:
                products = self._products_for_zone_pair(op_key, dep_zone, arr_zone)
                if products:
                    logger.info(
                        "Bus fare by coords: dep=%s arr=%s = %s:%s",
                        dep_stop_name,
                        arr_stop_name,
                        dep_zone,
                        arr_zone,
                    )
                    return products
        return {}

    @staticmethod
    def _nearest_zone(
        lat: float,
        lon: float,
        stop_coords: list[dict],
        radius_km: float = 0.1,
    ) -> str | None:
        """Find the zone of the nearest BODS stop within ``radius_km``."""
        origin = GeoPoint(lat, lon)
        best_dist = float("inf")
        best_zone: str | None = None
        for sc in stop_coords:
            pt = GeoPoint(sc["lat"], sc["lon"])
            d = origin.distance_km_to(pt)
            if d < best_dist:
                best_dist = d
                best_zone = sc.get("zone")
        if best_dist <= radius_km and best_zone:
            return best_zone
        return None

    # ------------------------------------------------------------------
    # Zone → products
    # ------------------------------------------------------------------

    def fares_for_zone_pair(
        self,
        dep_zone: str,
        arr_zone: str,
    ) -> dict[FareProductType, FareProduct]:
        """Look up fare products for a zone pair across all operators.

        Searches both ``dep:arr`` and ``arr:dep`` (fares are symmetric).
        Returns the first operator that has the pair.
        """
        self._load()
        for op_key in self._data:
            if op_key == "_meta":
                continue
            products = self._products_for_zone_pair(op_key, dep_zone, arr_zone)
            if products:
                return products
        return {}

    def _products_for_zone_pair(
        self,
        op_key: str,
        dep_zone: str,
        arr_zone: str,
    ) -> dict[FareProductType, FareProduct]:
        """Build ``FareProduct`` dict for a zone pair within one operator."""
        op_data = self._data.get(op_key, {})
        zone_fares: dict[str, dict] = op_data.get("zone_fares", {})
        zp = _zone_pair_key(dep_zone, arr_zone)
        pairs = zone_fares.get(zp)
        if pairs is None:
            # Try reversed pair (fares are symmetric for singles)
            zp = _zone_pair_key(arr_zone, dep_zone)
            pairs = zone_fares.get(zp)
        if pairs is None:
            return {}

        products: dict[FareProductType, FareProduct] = {}
        for json_key, price_val in pairs.items():
            ptype = _KEY_TO_TYPE.get(json_key)
            if ptype is None:
                continue
            price = Money(str(price_val), "GBP")
            products[ptype] = FareProduct(
                type=ptype,
                price=price,
                operator=op_key,
                zone_pair=zp,
            )
        return products


# ---------------------------------------------------------------------------
# Convenience: cheapest round trip
# ---------------------------------------------------------------------------


def cheapest_round_trip(
    fares: dict[FareProductType, FareProduct],
    national_max_single: Money | None = None,
) -> Money | None:
    """Cheapest way to do a weekday peak return trip.

    Considers: 2 × single (capped by ``national_max_single``), return
    ticket, day ticket.  Returns ``None`` if no fares are available.
    """
    if not fares:
        return None

    options: list[Money] = []

    if FareProductType.SINGLE in fares:
        single = fares[FareProductType.SINGLE].price
        if national_max_single is not None and single > national_max_single:
            single = national_max_single
        options.append(single * 2)

    if FareProductType.RETURN in fares:
        options.append(fares[FareProductType.RETURN].price)

    if FareProductType.DAY in fares:
        options.append(fares[FareProductType.DAY].price)

    return min(options) if options else None
