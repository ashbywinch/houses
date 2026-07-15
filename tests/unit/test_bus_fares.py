"""Tests for bus fare lookup and cheapest-round-trip logic.

Migrated from ``/tmp/old_test_enricher.py``::

    TestBusFareDailyCost     → TestCheapestRoundTrip
    TestComputeBusDailyCost  → TestCheapestRoundTrip
    TestKnownWrongBehaviours → (bus-fare portion) TestCheapestRoundTrip
    TestBusFareLookup        → TestBusJourneyRegistry._fares_for_stops
    TestStopToZoneMapping    → TestBusJourneyRegistry._stop_to_zone
    TestZonePairLookup       → TestBusJourneyRegistry._zone_pair_fares
    TestKnownWrongBehaviours → (stop-coords portion) TestBusJourneyRegistry._stop_coords
"""

from __future__ import annotations

from money import Money

from houses.bus_journey import (
    BusJourneyRegistry,
    FareProduct,
    FareProductType,
    cheapest_round_trip,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _fares_from_dict(products: dict[str, float], meta: dict | None = None) -> dict[FareProductType, FareProduct]:
    """Build a fare-product dict matching ``cheapest_round_trip`` input."""
    result: dict[FareProductType, FareProduct] = {}
    mapping = {
        "adult_single": FareProductType.SINGLE,
        "adult_return": FareProductType.RETURN,
        "adult_day": FareProductType.DAY,
    }
    for key, val in products.items():
        ptype = mapping.get(key)
        if ptype:
            result[ptype] = FareProduct(
                type=ptype,
                price=Money(str(val), "GBP"),
                operator="test",
                zone_pair="test:test",
            )
    return result


# ── cheapest_round_trip (pure function) ──────────────────────────────────────


class TestCheapestRoundTrip:
    """cheapest_round_trip — cheapest product covering a weekday peak return."""

    # ── from TestBusFareDailyCost ────────────────────────────────────────

    def test_uses_return_when_cheaper_than_2x_single(self):
        # adult_return £4.00 vs 2×single £5.00 → £4.00
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.50, "adult_return": 4.00}))
        assert cost == Money("4.00", "GBP")

    def test_uses_day_rider_when_cheapest(self):
        # adult_day £4.50 < 2×single £5.00 → £4.50
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.50, "adult_day": 4.50}))
        assert cost == Money("4.50", "GBP")

    def test_uses_2x_single_when_no_other_products(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.50}))
        assert cost == Money("5.00", "GBP")

    def test_national_cap_applied_to_single(self):
        # BODS single is £4.00, cap → £3.00, daily → £6.00
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 4.00}), Money("3.00", "GBP"))
        assert cost == Money("6.00", "GBP")

    def test_national_cap_below_cap(self):
        # BODS single £2.50 is below cap → used as-is
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.50}), Money("3.00", "GBP"))
        assert cost == Money("5.00", "GBP")

    def test_national_cap_not_set(self):
        # national_max_single is None → BODS single used as-is
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 4.00}))
        assert cost == Money("8.00", "GBP")

    # ── from TestComputeBusDailyCost ─────────────────────────────────────

    def test_single_only_doubled(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 1.5}))
        assert cost == Money("3.00", "GBP")

    def test_single_with_return(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 1.5, "adult_return": 2.5}))
        assert cost == Money("2.50", "GBP")

    def test_single_with_day_cheaper(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.0, "adult_day": 3.5}))
        assert cost == Money("3.50", "GBP")

    def test_day_more_expensive_than_double(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 0.9, "adult_day": 8.5}))
        assert cost == Money("1.80", "GBP")

    def test_national_cap_applied_before_doubling(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 3.5}), Money("3.00", "GBP"))
        assert cost == Money("6.00", "GBP")

    def test_return_cheaper_than_capped_double(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.0, "adult_return": 3.8}), Money("3.00", "GBP"))
        assert cost == Money("3.80", "GBP")

    def test_no_single_returns_none(self):
        cost = cheapest_round_trip({})
        assert cost is None

    def test_empty_fares_returns_none(self):
        cost = cheapest_round_trip({})
        assert cost is None

    def test_return_more_expensive_than_single_double(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 1.0, "adult_return": 2.5}))
        assert cost == Money("2.00", "GBP")

    def test_cap_makes_singles_cheaper_than_return(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 3.5, "adult_return": 5.0}), Money("2.00", "GBP"))
        assert cost == Money("4.00", "GBP")

    def test_return_only_no_single(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_return": 4.0}))
        assert cost == Money("4.00", "GBP")

    def test_day_only_no_single_or_return(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_day": 5.0}))
        assert cost == Money("5.00", "GBP")

    def test_all_products_day_is_cheapest(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.0, "adult_return": 3.5, "adult_day": 3.0}))
        assert cost == Money("3.00", "GBP")

    def test_all_products_return_is_cheapest(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 2.0, "adult_return": 2.5, "adult_day": 6.0}))
        assert cost == Money("2.50", "GBP")

    def test_all_products_singles_cheapest_even_with_cap(self):
        cost = cheapest_round_trip(_fares_from_dict({"adult_single": 1.0, "adult_return": 3.0, "adult_day": 4.0}))
        assert cost == Money("2.00", "GBP")

    # ── from TestKnownWrongBehaviours (bus-fare portion) ─────────────────

    def test_daily_cost_returns_return_when_no_single(self):
        """Fall back to return price when single is missing."""
        cost = cheapest_round_trip(_fares_from_dict({"adult_return": 4.0}))
        assert cost == Money("4.00", "GBP")

    def test_daily_cost_returns_day_when_no_single_no_return(self):
        """Use day price when single and return are missing."""
        cost = cheapest_round_trip(_fares_from_dict({"adult_day": 5.0}))
        assert cost == Money("5.00", "GBP")

    def test_daily_cost_uses_return_when_missing_single(self):
        """Return is used as-is (national cap only applies to single)."""
        cost = cheapest_round_trip(_fares_from_dict({"adult_return": 8.0}), Money("3.00", "GBP"))
        assert cost == Money("8.00", "GBP")


# ── BusJourneyRegistry (lazy-loaded from data/bus_fares.json) ────────────────


class TestBusJourneyRegistry:
    """BusJourneyRegistry — stop→zone mapping and zone-pair fare lookup.

    These integration tests exercise the real ``data/bus_fares.json`` file and
    the fuzzy-matching logic in ``fares_for_stops``.
    """

    _registry = BusJourneyRegistry()

    def _cost(self, dep: str, arr: str, dep_point=None, arr_point=None) -> Money | None:
        fare = self._registry.fares_for_stops(dep, arr, dep_point, arr_point)
        return cheapest_round_trip(fare, self._registry.national_max_single)

    # ── from TestStopToZoneMapping ───────────────────────────────────────

    def test_stop_to_zone_randolph_close(self):
        """Stop name → zone lookup returns fares for a known stop pair."""
        fares = self._registry.fares_for_stops("randolph close", "woking railway station")
        assert len(fares) > 0

    def test_stop_to_zone_reverse_direction(self):
        fares_rc = self._registry.fares_for_stops("randolph close", "woking railway station")
        fares_ws = self._registry.fares_for_stops("woking railway station", "randolph close")
        assert len(fares_rc) > 0
        assert len(fares_ws) > 0

    # ── from TestZonePairLookup ──────────────────────────────────────────

    def test_zone_pair_has_single_product(self):
        fares = self._registry.fares_for_stops("randolph close", "woking railway station")
        assert FareProductType.SINGLE in fares
        assert fares[FareProductType.SINGLE].price == Money("0.90", "GBP")

    def test_zone_pair_has_day_product(self):
        fares = self._registry.fares_for_stops("randolph close", "woking railway station")
        assert FareProductType.DAY in fares
        assert fares[FareProductType.DAY].price == Money("8.50", "GBP")

    def test_zone_pair_reverse_has_same_fares(self):
        fares = self._registry.fares_for_stops("woking railway station", "randolph close")
        assert FareProductType.SINGLE in fares
        assert fares[FareProductType.SINGLE].price == Money("0.90", "GBP")

    # ── from TestBusFareLookup (full pipeline) ───────────────────────────

    def test_randolph_close_to_woking_station(self):
        cost = self._cost("randolph close", "woking railway station")
        assert cost == Money("1.80", "GBP")

    def test_case_insensitive_matching(self):
        cost = self._cost("RANDOLPH CLOSE", "WOKING RAILWAY STATION")
        assert cost == Money("1.80", "GBP")

    def test_tfl_area_prefix_dep_match(self):
        cost = self._cost("Knaphill, Randolph Close", "Woking, Woking Railway Station")
        assert cost == Money("1.80", "GBP")

    def test_westfield_not_in_zone_fares(self):
        cost = self._cost("Westfield, Westfield Common", "Woking, Woking Railway Station")
        assert cost is not None, "Westfield→Woking should now match via fuzzy matching"

    def test_brookwood_to_woking(self):
        cost = self._cost("Brookwood, Brookwood Railway Station", "Woking, Woking Railway Station")
        assert cost == Money("3.00", "GBP")

    def test_fuzzy_match_periods(self):
        cost = self._cost("St. Johns, St. James Close", "Woking, Woking Railway Station")
        assert cost is not None

    def test_fuzzy_match_does_not_match_unrelated(self):
        cost = self._cost("Knaphill, Supermarket Car Park", "Woking, Woking Railway Station")
        assert cost is None, "Should not match unrelated stop 'supermarket car park'"

        cost2 = self._cost("North London Bus Stop", "Woking, Woking Railway Station")
        assert cost2 is None, "Should not match stop in entirely different area"

    def test_fuzzy_match_short_noise_words_rejected(self):
        cost = self._cost("Woking Station", "Woking, Woking Railway Station")
        assert cost is None, "'Woking Station' should not match 'station' (a different stop)"

    def test_unknown_stops_return_none(self):
        cost = self._cost("Unknown Stop", "Another Unknown")
        assert cost is None

    def test_same_stop_is_not_free(self):
        cost = self._cost("randolph close", "randolph close")
        assert cost is not None

    def test_reversed_direction(self):
        cost = self._cost("woking railway station", "randolph close")
        assert cost == Money("1.80", "GBP")

    def test_coord_fallback_without_coords_still_returns_none(self):
        cost = self._cost(
            "Unknown Stop",
            "Another Unknown",
            {"lat": 51.3, "lon": -0.5},
            {"lat": 51.31, "lon": -0.49},
        )
        assert cost is None

    # ── from TestKnownWrongBehaviours (stop_coords check) ────────────────

    def test_stop_coord_fallback_is_not_dead_code(self):
        """stop_coords should be populated from NaPTAN data during extraction."""
        registry = BusJourneyRegistry()
        _ = registry.national_max_single  # trigger lazy load
        scso = registry._data.get("Stagecoach_South", {})
        coords = scso.get("stop_coords", [])
        assert len(coords) > 0, "stop_coords empty — NaPTAN stop data not integrated or extraction needs re-run"
