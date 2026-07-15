"""Tests for houses/stations.py — Station short_name, find(), find_by_crs()."""

from houses.stations import Station
from houses.stations import find as find_station


class TestStationShortName:
    """Station.short_name — strip common station suffixes."""

    def test_rail_station(self):
        assert Station.short_name("Maidenhead Rail Station") == "Maidenhead"

    def test_underground_station(self):
        assert Station.short_name("Paddington Underground Station") == "Paddington"

    def test_generic_station(self):
        assert Station.short_name("Oxford Circus Station") == "Oxford Circus"

    def test_no_suffix(self):
        assert Station.short_name("Some Street, Town") == "Some Street, Town"

    def test_strips_london_prefix(self):
        assert Station.short_name("London Paddington Rail Station") == "Paddington"
        assert Station.short_name("London Waterloo Rail Station") == "Waterloo"

    def test_empty_string(self):
        assert Station.short_name("") == ""


class TestStationFind:
    """find_station — look up station coords from CSV by name."""

    def test_finds_didcot_parkway(self):
        """'Didcot Parkway Rail Station' should match 'Didcot Parkway' in stations.csv."""
        station = find_station("Didcot Parkway Rail Station")
        assert station is not None
        # Didcot Parkway is at ~51.611, -1.243 in stations.csv
        assert abs(station.location.lat - 51.611) < 0.02
        assert abs(station.location.lon + 1.243) < 0.02

    def test_returns_none_for_unknown(self):
        assert find_station("Some Fake Station") is None

    def test_strips_station_suffixes(self):
        # Should find Maidenhead in stations.csv (not "Maidenhead Rail Station")
        station = find_station("Maidenhead Rail Station")
        assert station is not None


class TestStationFindByCrs:
    """find_station — look up CRS from stations.csv by name."""

    def test_finds_woking(self):
        station = find_station("Woking Rail Station")
        assert station is not None
        assert station.crs == "WOK"

    def test_finds_maidenhead(self):
        station = find_station("Maidenhead Rail Station")
        assert station is not None
        assert station.crs == "MAI"

    def test_case_insensitive(self):
        station = find_station("woking rail station")
        assert station is not None
        assert station.crs == "WOK"

    def test_not_found_returns_none(self):
        assert find_station("Some Fake Station") is None
