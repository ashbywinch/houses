"""Tests for council tax extract/normalise functions — no API calls."""

from houses.council_tax import _extract_building, _normalise


class TestExtractBuilding:
    def test_street_number(self):
        result = _extract_building("94A Northbrook Street, Newbury, RG14 1AA")
        assert result == {"postcode": "RG14 1AA", "building_number": "94A"}

    def test_simple_number(self):
        result = _extract_building("10 Downing Street, London, SW1A 2AA")
        assert result == {"postcode": "SW1A 2AA", "building_number": "10"}

    def test_named_building(self):
        result = _extract_building("Buckingham Palace, London, SW1A 1AA")
        assert result == {"postcode": "SW1A 1AA", "building_name": "Buckingham Palace"}

    def test_flat_format(self):
        result = _extract_building("Flat 3, 123 High Street, Maidenhead, SL6 1AA")
        assert result == {"postcode": "SL6 1AA", "building_name": "Flat 3"}

    def test_no_postcode_in_address(self):
        result = _extract_building("10 Downing Street, London")
        assert result == {"postcode": "", "building_number": "10"}

    def test_empty_address(self):
        result = _extract_building("")
        assert result == {"postcode": "", "building_name": ""}


class TestNormalise:
    def test_uppercases(self):
        assert _normalise("abc123") == "ABC123"

    def test_strips_punctuation(self):
        assert _normalise("94A, Flat!") == "94A FLAT"

    def test_strips_whitespace(self):
        assert _normalise("  hello  ") == "HELLO"

    def test_removes_parentheses(self):
        assert _normalise("Flat (2nd Floor)") == "FLAT 2ND FLOOR"

    def test_empty_string(self):
        assert _normalise("") == ""


class TestLoadRates:
    """_load_rates loads the CSV and caches it."""

    def test_loads_woking_rate(self):
        from houses.council_tax import _load_rates

        rates = _load_rates()
        assert "woking" in rates, "Woking should be in the rates CSV"
        assert rates["woking"] == 2598.0
        assert rates["sheffield"] == 2510.0
        # Most rates should be over £1,000 (total area Band D)
        below_1000 = sum(1 for v in rates.values() if v < 1000)
        assert below_1000 < 10, f"{below_1000} authorities have rates under £1,000"

    def test_contains_billing_authorities(self):
        from houses.council_tax import _load_rates

        rates = _load_rates()
        assert len(rates) > 100, "Should have 100+ billing authorities"
        assert all(isinstance(v, float) for v in rates.values())
