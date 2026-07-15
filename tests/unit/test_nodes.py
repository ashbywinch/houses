from __future__ import annotations

from houses.model.geo import is_single_property_address


class TestSinglePropertyAddress:
    """Tests for is_single_property_address helper used by BestLocationNode."""

    def test_detects_single_property(self):
        assert is_single_property_address("31 Isambard Road, Southall UB2 4GN") is True
        assert is_single_property_address("London") is False
        assert is_single_property_address("10 High Street, London SW1V 2QQ") is True
        assert is_single_property_address("Maidenhead") is False
        assert is_single_property_address("") is False
        assert is_single_property_address(None) is False
