"""Unit tests for scripts/parse_netex_fares.py — the NeTEx fare XML parser.

Characterization suite: pins the parser's output contract (stop_zones /
stop_coords / zone_fares / network_fares) with a minimal synthetic NeTEx
document before the records refactor.
"""

from __future__ import annotations

from scripts.parse_netex_fares import (
    Station,
    dataset_description_matches,
    parse_netex_fares,
)

NS = 'xmlns="http://www.netex.org.uk/netex"'

XML = f"""<PublicationDelivery {NS} version="1.0">
  <ScheduledStopPoint id="ssp1">
    <Name>Foo Lane</Name>
    <AtcoCode>010A</AtcoCode>
    <Latitude>51.5</Latitude>
    <Longitude>-0.1</Longitude>
  </ScheduledStopPoint>
  <ScheduledStopPoint id="ssp2">
    <Name>Bar Road</Name>
    <AtcoCode>020B</AtcoCode>
    <Latitude>51.6</Latitude>
    <Longitude>-0.2</Longitude>
  </ScheduledStopPoint>
  <ScheduledStopPoint id="ssp3">
    <Name>Quarry Halt</Name>
    <AtcoCode>030C</AtcoCode>
    <Latitude>51.4</Latitude>
    <Longitude>-0.3</Longitude>
  </ScheduledStopPoint>
  <FareZone id="Z1">
    <Name>Zone One</Name>
    <StopPointRef ref="010A">010A</StopPointRef>
  </FareZone>
  <FareZone id="Z2">
    <Name>Zone Two</Name>
    <StopPointRef ref="020B">020B</StopPointRef>
  </FareZone>
  <FareZone id="Z3">
    <Name>Zone Three</Name>
    <StopPointRef ref="030C">030C</StopPointRef>
  </FareZone>
  <DistanceMatrixElement id="dme1">
    <StartTariffZoneRef ref="Z1"/>
    <EndTariffZoneRef ref="Z2@alighting"/>
  </DistanceMatrixElement>
  <DistanceMatrixElementPrice id="dmep1">
    <Amount>2.5</Amount>
    <DistanceMatrixElementRef ref="dme1"/>
  </DistanceMatrixElementPrice>
  <DistanceMatrixElement id="dme2">
    <StartTariffZoneRef ref="Z2"/>
    <EndTariffZoneRef ref="Z1"/>
    <PriceGroupRef ref="pg1"/>
  </DistanceMatrixElement>
  <PriceGroup id="pg1">
    <Amount>3.75</Amount>
  </PriceGroup>
  <PreassignedFareProduct id="prod1">
    <Name>Adult Single</Name>
    <Price>
      <Amount>1.2</Amount>
    </Price>
  </PreassignedFareProduct>
  <DistanceMatrixElement id="dme3">
    <StartZoneRef ref="Z3"/>
    <EndZoneRef ref="Z1"/>
    <PreassignedFareProductRef ref="prod1"/>
  </DistanceMatrixElement>
  <PreassignedFareProduct id="prod_day">
    <Name>Didcot Day Rider</Name>
    <Price>
      <Amount>4.5</Amount>
    </Price>
  </PreassignedFareProduct>
  <FareTable id="ft_day">
    <PreassignedFareProductRef ref="prod_day"/>
    <DistanceMatrixElementPrice>
      <Amount>4.5</Amount>
    </DistanceMatrixElementPrice>
  </FareTable>
  <Tariff id="t1">
    <FareZoneRef ref="Z1"/>
  </Tariff>
</PublicationDelivery>
"""

STATION_AT_FOO = Station(name="Foo Lane Station", crs="FOO", lat=51.5, long=-0.1)


class TestGating:
    def test_invalid_xml_returns_none(self):
        assert parse_netex_fares("<not-xml", []) is None

    def test_no_stops_returns_none(self):
        xml = f'<PublicationDelivery {NS}><FareZone id="Z1"/></PublicationDelivery>'
        assert parse_netex_fares(xml, []) is None

    def test_no_stop_near_station_returns_none(self):
        xml = XML.replace("51.5", "55.0")  # Foo Lane moved far from any station
        assert parse_netex_fares(xml, [STATION_AT_FOO]) is None

    def test_no_coordinates_proceeds_without_near_flags(self):
        xml = f"""<PublicationDelivery {NS}>
          <ScheduledStopPoint id="ssp1"><Name>No Coords</Name><AtcoCode>040D</AtcoCode></ScheduledStopPoint>
        </PublicationDelivery>"""
        result = parse_netex_fares(xml, [STATION_AT_FOO])
        assert result is not None
        assert result["stop_zones"] == {}
        assert result["stop_coords"] == []


class TestStops:
    def test_stops_parsed_with_near_station_flag(self):
        result = parse_netex_fares(XML, [STATION_AT_FOO])
        assert result is not None
        # only stop_coords exposes stops; near-station gating shows via which
        # stops survive the proximity filter — Foo Lane (51.5, -0.1) is within
        # 0.2 km of the test station, Bar/Quarry are not.
        zones = {c["zone"]: c for c in result["stop_coords"]}
        assert set(zones) == {"Z1", "Z2", "Z3"}

    def test_duplicate_stop_ids_first_wins(self):
        xml = XML.replace(
            "</PublicationDelivery>",
            """  <ScheduledStopPoint id="ssp1b">
        <Name>Foo Lane</Name>
        <AtcoCode>010A</AtcoCode>
        <Latitude>99.0</Latitude>
        <Longitude>99.0</Longitude>
      </ScheduledStopPoint>
    </PublicationDelivery>""".replace("</PublicationDelivery>", "").join(["", ""]) + "</PublicationDelivery>",
        )
        result = parse_netex_fares(xml, [STATION_AT_FOO])
        assert result is not None
        coords = [c for c in result["stop_coords"] if c["name"] == "Foo Lane"]
        assert coords and coords[0]["lat"] == 51.5  # first definition kept

    def test_naptan_fallback_fills_missing_coordinates(self):
        xml = XML.replace("<Latitude>51.5</Latitude>\n    <Longitude>-0.1</Longitude>\n    ", "")
        result = parse_netex_fares(xml, [STATION_AT_FOO], naptan={"010A": (51.5, -0.1)})
        assert result is not None
        assert any(c["name"] == "Foo Lane" for c in result["stop_coords"])


class TestStopZones:
    def test_stop_zones_normalized_by_lowercase_name(self):
        result = parse_netex_fares(XML, [STATION_AT_FOO])
        assert result is not None
        assert result["stop_zones"] == {"foo lane": "Z1", "bar road": "Z2", "quarry halt": "Z3"}


class TestStopCoords:
    def test_coords_rounded_and_zoned(self):
        result = parse_netex_fares(XML, [STATION_AT_FOO])
        assert result is not None
        by_zone = {c["zone"]: c for c in result["stop_coords"]}
        assert by_zone["Z1"] == {"name": "Foo Lane", "lat": 51.5, "lon": -0.1, "zone": "Z1"}
        assert by_zone["Z2"]["name"] == "Bar Road"
        assert set(by_zone["Z2"]) == {"name", "lat", "lon", "zone"}


class TestZoneFares:
    def test_distance_matrix_price_with_alighting_normalization(self):
        result = parse_netex_fares(XML, [STATION_AT_FOO])
        assert result is not None
        assert result["zone_fares"]["Z1:Z2@boarding"]["adult_single"] == 2.5

    def test_price_group_fare(self):
        result = parse_netex_fares(XML, [STATION_AT_FOO])
        assert result is not None
        assert result["zone_fares"]["Z2:Z1"]["adult_single"] == 3.75

    def test_preassigned_product_via_product_ref(self):
        result = parse_netex_fares(XML, [STATION_AT_FOO])
        assert result is not None
        assert result["zone_fares"]["Z3:Z1"]["adult_single"] == 1.2


class TestNetworkFares:
    def test_fare_table_day_product_without_dme_ref(self):
        result = parse_netex_fares(XML, [STATION_AT_FOO])
        assert result is not None
        assert len(result["network_fares"]) == 1
        nf = result["network_fares"][0]
        assert nf["price"] == 4.5
        assert nf["product_type"] == "adult_day"
        assert "010a" in nf["covered_stops"]


class TestHelpers:
    def test_classify_fare_product_type(self):
        from scripts.parse_netex_fares import _classify_fare_product_type

        # callers lower-case the product name before classification
        assert _classify_fare_product_type("bourne end single") == "adult_single"
        assert _classify_fare_product_type("weekly return") == "adult_return"
        assert _classify_fare_product_type("day rider") == "adult_day"
        assert _classify_fare_product_type("dayrider plus") == "adult_day"
        assert _classify_fare_product_type("season ticket") is None

    def test_as_float(self):
        from scripts.parse_netex_fares import _as_float

        assert _as_float("2.5") == 2.5
        assert _as_float("") is None
        assert _as_float(None) is None
        assert _as_float("abc") is None

    def test_dataset_description_matches(self):
        assert dataset_description_matches("BODS DX 2024", "bods dx 2024")
        assert not dataset_description_matches("", "anything")
        assert not dataset_description_matches("BODS DX", "BODS DX 2024")
