"""Tests for enrichment logic."""

from houses.enricher import _OUTCODE_RE, FEE_PAYING_TYPES, _boys_eligible, _haversine_km


class TestOutcodeDetection:
    def test_outcode_matches_sl6(self):
        assert _OUTCODE_RE.match("SL6")

    def test_outcode_matches_sw1e(self):
        assert _OUTCODE_RE.match("SW1E")

    def test_outcode_matches_ec3a(self):
        assert _OUTCODE_RE.match("EC3A")

    def test_full_postcode_does_not_match(self):
        assert not _OUTCODE_RE.match("RG14 1AA")

    def test_full_postcode_no_space_does_not_match(self):
        assert not _OUTCODE_RE.match("RG141AA")

    def test_empty_does_not_match(self):
        assert not _OUTCODE_RE.match("")

    def test_not_a_postcode_does_not_match(self):
        assert not _OUTCODE_RE.match("not a postcode")


class TestBoysEligible:
    def test_mixed_gender_eligible(self):
        assert _boys_eligible({"Gender (name)": "Mixed", "TypeOfEstablishment (name)": "Community School"})

    def test_boys_gender_eligible(self):
        assert _boys_eligible({"Gender (name)": "Boys", "TypeOfEstablishment (name)": "Academy Converter"})

    def test_girls_gender_ineligible(self):
        assert not _boys_eligible({"Gender (name)": "Girls", "TypeOfEstablishment (name)": "Community School"})

    def test_independent_school_ineligible(self):
        assert not _boys_eligible({"Gender (name)": "Mixed", "TypeOfEstablishment (name)": "Independent School"})

    def test_missing_fields_returns_false(self):
        assert not _boys_eligible({})


class TestHaversine:
    def test_same_point_returns_zero(self):
        # Same lat/lng should be 0 km
        dist = _haversine_km(51.5, -0.13, 51.5, -0.13)
        assert dist == 0.0

    def test_known_distance(self):
        # London to Brighton ~75km
        dist = _haversine_km(51.5, -0.13, 50.83, -0.14)
        assert 70 < dist < 80

    def test_symmetric(self):
        d1 = _haversine_km(51.5, -0.13, 52.0, 0.0)
        d2 = _haversine_km(52.0, 0.0, 51.5, -0.13)
        assert abs(d1 - d2) < 0.001


class TestFeePayingTypes:
    def test_includes_known_private_types(self):
        assert "independent school" in FEE_PAYING_TYPES
        assert "other independent school" in FEE_PAYING_TYPES

    def test_excludes_public_types(self):
        assert "community school" not in FEE_PAYING_TYPES
        assert "academy converter" not in FEE_PAYING_TYPES
