"""Tests for BODS NeTEx bus fare XML parsing.

Migrated from ``/tmp/old_test_enricher.py``::

    TestNeTExParsing → TestParseNeTExFares
"""

from __future__ import annotations

from pathlib import Path

from houses.stations import Station


class TestParseNeTExFares:
    """parse_netex_fares — extracts stop zones and fares from BODS NeTEx XML."""

    _STATIONS_CACHE: list[Station] | None = None

    @classmethod
    def _stations(cls) -> list[Station]:
        if cls._STATIONS_CACHE is None:
            import csv

            cls._STATIONS_CACHE = []
            with Path("data/stations.csv").open(newline="") as f:
                for row in csv.DictReader(f):
                    cls._STATIONS_CACHE.append(
                        Station(
                            name=row["stationName"],
                            crs=row["crsCode"],
                            location=None,
                        )
                    )
        return cls._STATIONS_CACHE

    def test_parses_scso_stops_and_zones(self):
        """Stagecoach South dataset should find stops and zones."""
        xml = (Path("tests/fixtures/bods") / "scso_sample.xml").read_text()
        from scripts.extract_bus_fares import parse_netex_fares

        result = parse_netex_fares(xml, self._stations())
        assert result is not None
        assert len(result.get("stop_zones", {})) >= 1

    def test_parses_scso_zone_prices(self):
        """Stagecoach South dataset should extract adult_single prices for zone pairs.

        The real BODS fare data uses StartTariffZoneRef/EndTariffZoneRef
        and nests prices inside Tariff → FareStructureElement → PriceGroup
        instead of the simple AC Williams format.
        """
        xml = (Path("tests/fixtures/bods") / "scso_sample.xml").read_text()
        from scripts.extract_bus_fares import parse_netex_fares

        result = parse_netex_fares(xml, self._stations())
        assert result is not None
        fares = result.get("zone_fares", {})
        assert len(fares) >= 1
        any_single = any("adult_single" in v for v in fares.values())
        assert any_single, "No zone fare has an adult_single price"
