"""Tests for RailFareRegistry — lazy loading, station lookup, fare lookup."""

from pathlib import Path

from money import Money

from houses.geo import GeoPoint
from houses.rail_fares import RailFare
from houses.rail_fares import RailFareRegistry as Registry
from houses.stations import StationRegistry


def _make_registry(stations_csv: Path, fares_csv: Path) -> Registry:
    return Registry(
        station_registry=StationRegistry(_stations_csv=stations_csv),
        _fares_csv=fares_csv,
    )


# ── RailFare dataclass ──────────────────────────────────────────────────


def test_rail_fare_dataclass():
    fare = RailFare(origin_crs="WOK", dest_crs="VIC", single_fare_gbp=Money("17.00", "GBP"))
    assert fare.origin_crs == "WOK"
    assert fare.dest_crs == "VIC"
    assert fare.single_fare_gbp == Money("17.00", "GBP")


# ── fare_between ────────────────────────────────────────────────────────


def test_fare_between_exact_match(tmp_path):
    csv = tmp_path / "fares.csv"
    csv.write_text("origin_crs,dest_crs,single_fare_gbp\nWOK,VIC,17.00\n")
    stn = tmp_path / "stations.csv"
    stn.write_text("stationName,crsCode,lat,long\nWoking,WOK,51.317,-0.556\nVictoria Station,VIC,51.495,-0.144\n")

    reg = _make_registry(stn, csv)
    origin = reg.find_station_by_crs("WOK")
    dest = reg.find_station_by_crs("VIC")
    assert origin and dest
    assert reg.fare_between(origin, dest) == Money("17.00", "GBP")


def test_fare_between_reverse_match(tmp_path):
    csv = tmp_path / "fares.csv"
    csv.write_text("origin_crs,dest_crs,single_fare_gbp\nVIC,WOK,17.00\n")
    stn = tmp_path / "stations.csv"
    stn.write_text("stationName,crsCode,lat,long\nWoking,WOK,51.317,-0.556\nVictoria Station,VIC,51.495,-0.144\n")

    reg = _make_registry(stn, csv)
    origin = reg.find_station_by_crs("WOK")
    dest = reg.find_station_by_crs("VIC")
    assert origin and dest
    assert reg.fare_between(origin, dest) == Money("17.00", "GBP")


def test_fare_between_no_match(tmp_path):
    csv = tmp_path / "fares.csv"
    csv.write_text("origin_crs,dest_crs,single_fare_gbp\nWOK,PAD,15.00\n")
    stn = tmp_path / "stations.csv"
    stn.write_text(
        "stationName,crsCode,lat,long\n"
        "Woking,WOK,51.317,-0.556\n"
        "Victoria Station,VIC,51.495,-0.144\n"
        "Paddington,PAD,51.515,-0.176\n"
    )

    reg = _make_registry(stn, csv)
    origin = reg.find_station_by_crs("WOK")
    dest = reg.find_station_by_crs("VIC")
    assert origin and dest
    assert reg.fare_between(origin, dest) is None


# ── nearest_station ─────────────────────────────────────────────────────


def test_nearest_station_returns_station(tmp_path):
    csv = tmp_path / "stations.csv"
    csv.write_text("stationName,crsCode,lat,long\nWoking,WOK,51.317,-0.556\n")
    fares = tmp_path / "fares.csv"
    fares.write_text("origin_crs,dest_crs,single_fare_gbp\nWOK,VIC,0.00\n")

    reg = _make_registry(csv, fares)
    result = reg.nearest_station(GeoPoint(51.317, -0.556))
    assert result is not None
    assert result.crs == "WOK"
    assert result.name == "Woking"


def test_nearest_station_returns_closest(tmp_path):
    csv = tmp_path / "stations.csv"
    csv.write_text("stationName,crsCode,lat,long\nWoking,WOK,51.317,-0.556\nBrookwood,BKO,51.303,-0.636\n")
    fares = tmp_path / "fares.csv"
    fares.write_text("origin_crs,dest_crs,single_fare_gbp\nWOK,VIC,0.00\n")

    reg = _make_registry(csv, fares)
    result = reg.nearest_station(GeoPoint(51.317, -0.556))
    assert result is not None
    assert result.crs == "WOK"


def test_nearest_station_no_data(tmp_path):
    csv = tmp_path / "stations.csv"
    csv.write_text("stationName,crsCode,lat,long\n")  # header only, no data
    fares = tmp_path / "fares.csv"
    fares.write_text("origin_crs,dest_crs,single_fare_gbp\nWOK,VIC,0.00\n")

    reg = _make_registry(csv, fares)
    result = reg.nearest_station(GeoPoint(51.317, -0.556))
    assert result is None
