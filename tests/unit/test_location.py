"""Tests for PropertyLocation — geocoding resolution with outcode upgrade."""

from __future__ import annotations

from houses.location import PropertyLocation


class TestUpgradeAddress:
    """PropertyLocation._upgrade_address — replace trailing outcode with full postcode."""

    def setup_method(self) -> None:
        self.upgrade = PropertyLocation._upgrade_address

    def test_replaces_trailing_outcode(self):
        """'Grand Drive, London, SW20' + 'SW20 9NB' → 'Grand Drive, London, SW20 9NB'."""
        result = self.upgrade("Grand Drive, London, SW20", "SW20 9NB")
        assert result == "Grand Drive, London, SW20 9NB"

    def test_already_has_full_postcode(self):
        """Address already contains full postcode — unchanged."""
        result = self.upgrade("Grand Drive, London, SW20 9NB", "SW20 9NB")
        assert result == "Grand Drive, London, SW20 9NB"

    def test_no_trailing_postcode(self):
        """Address has no trailing postcode pattern — unchanged."""
        result = self.upgrade("31 Isambard Road, Southall", "UB2 4GN")
        assert result == "31 Isambard Road, Southall"

    def test_postcode_is_itself_an_outcode(self):
        """Postcode param is an outcode (not full) — no upgrade possible."""
        result = self.upgrade("Grand Drive, London, SW20", "SW20")
        assert result == "Grand Drive, London, SW20"

    def test_empty_address(self):
        """Empty address returns empty string."""
        result = self.upgrade("", "SW20 9NB")
        assert result == ""

    def test_empty_postcode(self):
        """Empty postcode — unchanged."""
        result = self.upgrade("Grand Drive, London, SW20", "")
        assert result == "Grand Drive, London, SW20"

    def test_outcode_nw1(self):
        """NW1 is a valid outcode and gets upgraded."""
        result = self.upgrade("London, NW1", "NW1 7ER")
        assert result == "London, NW1 7ER"

    def test_trailing_text_not_an_outcode(self):
        """Trailing text after comma that isn't a valid outcode — unchanged."""
        result = self.upgrade("Some Place, ABC", "AB1 2CD")
        assert result == "Some Place, ABC"

    def test_trailing_outcode_matches_exactly(self):
        """Outcode at end matches pattern exactly (e.g. 'SL6' not 'SL6 1AA')."""
        result = self.upgrade("Maidenhead, SL6", "SL6 1AA")
        assert result == "Maidenhead, SL6 1AA"

    def test_lowercase_outcode(self):
        """Outcode in address is lowercase — still matched."""
        result = self.upgrade("Grand Drive, London, sw20", "SW20 9NB")
        assert result == "Grand Drive, London, SW20 9NB"

    def test_address_with_comma_outcode_commas(self):
        """Multiple commas in address — only trailing outcode replaced."""
        result = self.upgrade("Flat 3, 123 High Street, London, SW20", "SW20 9NB")
        assert result == "Flat 3, 123 High Street, London, SW20 9NB"


class TestIsOutcode:
    """is_outcode — validate partial postcode (outcode) strings."""

    def test_outcode_matches_sl6(self):
        from houses.location import is_outcode

        assert is_outcode("SL6")

    def test_outcode_matches_sw1e(self):
        from houses.location import is_outcode

        assert is_outcode("SW1E")

    def test_outcode_matches_ec3a(self):
        from houses.location import is_outcode

        assert is_outcode("EC3A")

    def test_full_postcode_does_not_match(self):
        from houses.location import is_outcode

        assert not is_outcode("RG14 1AA")

    def test_full_postcode_no_space_does_not_match(self):
        from houses.location import is_outcode

        assert not is_outcode("RG141AA")

    def test_empty_does_not_match(self):
        from houses.location import is_outcode

        assert not is_outcode("")

    def test_not_a_postcode_does_not_match(self):
        from houses.location import is_outcode

        assert not is_outcode("not a postcode")


class TestEndPostcodePattern:
    """_END_PC_RE — strips trailing postcodes from address strings."""

    def test_full_postcode_matches(self):
        from houses.location import _END_PC_RE

        assert _END_PC_RE.search(", RG14 1AA")

    def test_outcode_matches(self):
        from houses.location import _END_PC_RE

        assert _END_PC_RE.search(", SL6")

    def test_london_outcode_matches(self):
        from houses.location import _END_PC_RE

        assert _END_PC_RE.search(", SW1E")

    def test_no_postcode_does_not_match(self):
        from houses.location import _END_PC_RE

        assert not _END_PC_RE.search("Some Road, Town")

    def test_empty_string_does_not_match(self):
        from houses.location import _END_PC_RE

        assert not _END_PC_RE.search("")

    def test_postcode_without_comma_does_not_match(self):
        """Pattern requires a leading comma — a bare postcode shouldn't match."""
        from houses.location import _END_PC_RE

        assert not _END_PC_RE.search("RG14 1AA")
