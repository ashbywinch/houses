"""Tests for parking cost lookup via CarParkRegistry."""

from pathlib import Path

import pytest
from money import Money

from houses.car_park import (
    CarPark,
    CarParkRegistry,
    _apcoa_location_urls,
    _make_slug,
    _parse_apcoa_location_page,
    _parse_apcoa_prebook_listing,
)
from houses.geo import GeoPoint
from houses.stations import Station


class TestParseApcoaLocationPage:
    """_parse_apcoa_location_page with real APCOA page fixture."""

    def _load_fixture(self, name: str) -> tuple[str, str]:
        path = Path("tests/fixtures/apcoa_pages") / f"{name}.txt"
        text = path.read_text()
        lines = text.split("\n")
        title_line = next((ln for ln in lines if ln.startswith("TITLE:")), "TITLE: Unknown")
        title = title_line.replace("TITLE:", "", 1).strip()
        # Extract page text after "---TEXT---" marker
        text_start = text.find("---TEXT---")
        page_text = text[text_start + len("---TEXT---") :].strip() if text_start >= 0 else text
        return page_text, title

    def test_parse_bourne_end(self):
        """Real APCOA page for Bourne End yields name, address, and price."""
        page_text, title = self._load_fixture("bourne_end")
        result = _parse_apcoa_location_page(page_text, title)

        assert result is not None
        assert "Bourne End Station" in result["name"]
        assert result["address"] is not None
        assert "Station Road" in result["address"]
        assert result["price"] == 4.0

    def test_parse_name_from_title(self):
        """Name is extracted from the page title before ' - APCOA'."""
        result = _parse_apcoa_location_page(
            "Parking tariff\n\nDaily Rate: £12.80",
            "Woking Station Car Park - APCOA",
        )
        assert result is not None
        assert result["name"] == "Woking Station Car Park"

    def test_parse_no_tariff_returns_none(self):
        """Page without 'Parking tariff' section returns None."""
        result = _parse_apcoa_location_page(
            "Some random page text without pricing",
            "Random Page",
        )
        assert result is None

    def test_parse_no_price_returns_none(self):
        """Tariff section without extractable price returns None."""
        result = _parse_apcoa_location_page(
            "Parking tariff\n\nPermit Holders Only",
            "Test - APCOA",
        )
        assert result is None

    def test_parse_empty_page_returns_none(self):
        """Completely empty page text returns None."""
        result = _parse_apcoa_location_page("", "Empty Page")
        assert result is None


class TestParseApcoaPrebookListing:
    """_parse_apcoa_prebook_listing with realistic page text."""

    def test_parse_listing_with_name_address_price(self):
        """Typical prebook listing with name, address, and From £X.XX."""
        text = "APCOA Maidenhead\nStation Approach, Maidenhead\nFrom £9.00 per day\nMore details"
        result = _parse_apcoa_prebook_listing(text)
        assert result is not None
        assert result["name"] == "APCOA Maidenhead"
        assert result["address"] == "Station Approach, Maidenhead"
        assert result["price"] == 9.0

    def test_parse_listing_no_price_returns_none(self):
        text = "APCOA Maidenhead\nStation Approach, Maidenhead\nNo price info"
        result = _parse_apcoa_prebook_listing(text)
        assert result is None

    def test_parse_listing_empty_returns_none(self):
        result = _parse_apcoa_prebook_listing("")
        assert result is None


class TestMakeSlug:
    """_make_slug URL slug generation."""

    def test_basic_name(self):
        assert _make_slug("Woking") == "woking"

    def test_with_apostrophe(self):
        assert _make_slug("Stoke D'Abernon") == "stoke-dabernon"

    def test_with_ampersand(self):
        assert _make_slug("Cobham & Stoke") == "cobham-and-stoke"

    def test_multi_word_name(self):
        assert _make_slug("Bourne End") == "bourne-end"

    def test_parkway(self):
        assert _make_slug("Didcot Parkway") == "didcot-parkway"


class TestApcoaLocationUrls:
    """_apcoa_location_urls URL generation."""

    def test_single_city_slug(self):
        urls = _apcoa_location_urls("Woking")
        assert len(urls) == 2
        assert all("woking" in u for u in urls)

    def test_two_city_slugs(self):
        urls = _apcoa_location_urls("Bourne End")
        assert len(urls) == 4
        assert any("bourne-end" in u for u in urls)
        assert any("bourne" in u and "bourne-end" not in u for u in urls)


class TestCarPark:
    """CarPark dataclass behaviour."""

    def test_name_defaults(self):
        cp = CarPark(name="Test Car Park")
        assert cp.name == "Test Car Park"
        assert cp.daily_cost is None
        assert cp.address is None

    def test_with_cost(self):
        cp = CarPark(name="Test", daily_cost=Money("12.80", "GBP"))
        assert float(cp.daily_cost.amount) == 12.80

    def test_with_address(self):
        cp = CarPark(name="Test", address="123 High Street")
        assert cp.address == "123 High Street"


class TestCarParkRegistry:
    """CarParkRegistry CSV loading and lookup."""

    def test_no_csv_file_returns_empty(self, monkeypatch):
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", Path("/tmp/nonexistent_parking_rates.csv"))
        registry = CarParkRegistry()
        registry._load()
        # All lookups return None for unknown stations
        station = Station(name="Any", crs="ANY", location=GeoPoint(0, 0))
        assert registry.find_car_park(station) is None

    def test_lookup_known_station_by_name(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nWoking,WOK,12.80\n")
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

        registry = CarParkRegistry()
        station = Station(name="Woking", crs="WOK", location=GeoPoint(51.3, -0.5))
        car_park = registry.find_car_park(station)

        assert car_park is not None
        assert car_park.name == "Woking Station Car Park"  # derived from station name
        assert car_park.daily_cost == Money("12.80", "GBP")

    def test_lookup_free_station(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nMarlow,MLW,0.0\n")
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

        registry = CarParkRegistry()
        station = Station(name="Marlow", crs="MLW", location=GeoPoint(51.5, -0.8))
        car_park = registry.find_car_park(station)

        assert car_park is not None
        assert car_park.daily_cost == Money("0.0", "GBP")

    def test_lookup_unknown_station(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nMarlow,MLW,0.0\n")
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

        registry = CarParkRegistry()
        station = Station(name="Unknown", crs="ZZZ", location=GeoPoint(0, 0))
        car_park = registry.find_car_park(station)

        assert car_park is None

    def test_lookup_blank_cost_returns_car_park_with_none_cost(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nMarlow,MLW,\n")
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

        registry = CarParkRegistry()
        station = Station(name="Marlow", crs="MLW", location=GeoPoint(51.5, -0.8))
        car_park = registry.find_car_park(station)

        assert car_park is not None
        assert car_park.daily_cost is None  # blank → None, not 0

    def test_lookup_by_crs_fallback(self, tmp_path, monkeypatch):
        """When name doesn't match the CSV key, falls back to CRS."""
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nCobham & Stoke Dabernon,CSD,8.10\n")
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

        registry = CarParkRegistry()
        # TfL returns "Cobham & Stoke D'Abernon" — name differs from CSV
        station = Station(name="Cobham & Stoke D'Abernon", crs="CSD", location=GeoPoint(51.3, -0.4))
        car_park = registry.find_car_park(station)

        # Found by CRS fallback
        assert car_park is not None
        assert car_park.daily_cost == Money("8.10", "GBP")

    def test_lookup_by_crs_missing(self, tmp_path, monkeypatch):
        """When neither name nor CRS matches, returns None."""
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nWoking,WOK,12.80\n")
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

        registry = CarParkRegistry()
        station = Station(name="Reading", crs="RDG", location=GeoPoint(51.4, -1.0))
        car_park = registry.find_car_park(station)

        assert car_park is None

    def test_load_handles_old_csv_format(self, tmp_path, monkeypatch):
        """Old CSV without car_park_name or address columns still works."""
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nMaidenhead,MAI,9.00\n")
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

        registry = CarParkRegistry()
        station = Station(name="Maidenhead", crs="MAI", location=GeoPoint(51.5, -0.7))
        car_park = registry.find_car_park(station)

        assert car_park is not None
        assert car_park.name == "Maidenhead Station Car Park"  # derived name
        assert car_park.daily_cost == Money("9.00", "GBP")
        assert car_park.address is None

    def test_load_handles_new_csv_format(self, tmp_path, monkeypatch):
        """New CSV with car_park_name and address columns."""
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text(
            "station_name,crs,daily_cost_gbp,car_park_name,address\n"
            "Maidenhead,MAI,9.00,APCOA Maidenhead,35 High Street Maidenhead\n"
        )
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

        registry = CarParkRegistry()
        station = Station(name="Maidenhead", crs="MAI", location=GeoPoint(51.5, -0.7))
        car_park = registry.find_car_park(station)

        assert car_park is not None
        assert car_park.name == "APCOA Maidenhead"
        assert car_park.address == "35 High Street Maidenhead"

    def test_persist_results_writes_to_csv(self, tmp_path, monkeypatch):
        """_persist_results writes car park details to CSV and loads back on next instance."""

        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp,car_park_name,address\n")
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

        registry = CarParkRegistry()
        station = Station(name="Woking", crs="WOK", location=GeoPoint(51.3, -0.5))
        car_park = CarPark(
            name="Woking Station Car Park",
            daily_cost=Money("12.80", "GBP"),
            address="Woking Station Approach",
        )

        registry._persist_results(station, car_park)

        # Verify CSV was written
        lines = csv_path.read_text().strip().split("\n")
        assert len(lines) == 2, f"Expected header + 1 row, got {len(lines)} lines"
        assert "Woking" in lines[1], f"Expected Woking in CSV, got:\n{lines[1]}"

        # Create a NEW registry instance and verify the data loads back
        registry2 = CarParkRegistry()
        found = registry2.find_car_park(station)
        assert found is not None, "Should find persisted car park"
        assert found.name == "Woking Station Car Park"
        assert found.daily_cost == Money("12.80", "GBP")
        assert found.address == "Woking Station Approach"

    # ── APCOA methods (mock the internal scraper) ─────────────────────

    @pytest.mark.asyncio
    async def test_load_costs_known_station_no_cost(self, tmp_path, monkeypatch):
        """Known car park with no cost gets cost loaded from APCOA."""
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nWoking,WOK,\n")
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

        async def _mock_apcoa(_station):
            return {
                "name": "Woking Station Car Park",
                "address": "Woking Station Approach",
                "price": 12.80,
            }

        registry = CarParkRegistry()
        monkeypatch.setattr(registry, "_apcoa_lookup", _mock_apcoa)

        station = Station(name="Woking", crs="WOK", location=GeoPoint(51.3, -0.5))
        car_park = registry.find_car_park(station)
        assert car_park is not None
        assert car_park.daily_cost is None  # blank before load

        result = await registry.load_costs(car_park, station)
        assert result.is_succeeded
        updated = result.value_or_none()
        assert updated is car_park  # same object, mutated
        assert updated.daily_cost == Money("12.80", "GBP")
        assert updated.address == "Woking Station Approach"

    @pytest.mark.asyncio
    async def test_load_costs_apcoa_returns_none(self, tmp_path, monkeypatch):
        """When APCOA returns nothing, load_costs returns impossible."""
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nWoking,WOK,\n")
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

        async def _mock_apcoa(_station):
            return None

        registry = CarParkRegistry()
        monkeypatch.setattr(registry, "_apcoa_lookup", _mock_apcoa)

        station = Station(name="Woking", crs="WOK", location=GeoPoint(51.3, -0.5))
        car_park = registry.find_car_park(station)

        result = await registry.load_costs(car_park, station)
        assert result.is_impossible
        assert "No APCOA rate" in result.reason

    @pytest.mark.asyncio
    async def test_add_nearest_car_park_for_unknown_station(self, tmp_path, monkeypatch):
        """Station not in CSV gets a new CarPark from APCOA."""
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\n")
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

        async def _mock_apcoa(_station):
            return {
                "name": "Reading Station Car Park",
                "address": "Reading Station Rd",
                "price": 15.00,
            }

        registry = CarParkRegistry()
        monkeypatch.setattr(registry, "_apcoa_lookup", _mock_apcoa)

        station = Station(name="Reading", crs="RDG", location=GeoPoint(51.4, -1.0))
        assert registry.find_car_park(station) is None  # not in CSV

        result = await registry.add_nearest_car_park_for(station)
        assert result.is_succeeded
        car_park = result.value_or_none()
        assert car_park.name == "Reading Station Car Park"
        assert car_park.daily_cost == Money("15.00", "GBP")
        assert car_park.address == "Reading Station Rd"

        # Now should be findable
        found = registry.find_car_park(station)
        assert found is not None
        assert found.daily_cost == Money("15.00", "GBP")

    @pytest.mark.asyncio
    async def test_add_nearest_car_park_apcoa_returns_none(self, tmp_path, monkeypatch):
        """When APCOA returns nothing, add_nearest returns impossible."""
        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\n")
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

        async def _mock_apcoa(_station):
            return None

        registry = CarParkRegistry()
        monkeypatch.setattr(registry, "_apcoa_lookup", _mock_apcoa)

        station = Station(name="Reading", crs="RDG", location=GeoPoint(51.4, -1.0))
        result = await registry.add_nearest_car_park_for(station)
        assert result.is_impossible
        assert "No APCOA car park" in result.reason
