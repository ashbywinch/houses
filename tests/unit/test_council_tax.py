"""Tests for council tax extract/normalise functions — no API calls."""

import pytest

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
        assert result == {"postcode": "SL6 1AA", "unit": "Flat 3", "building_name": "123 High Street"}

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


class TestLookupExactMatchPriority:
    """An exact match must win — a separate dwelling at the same number
    (annexe/flat) must not make the exact address ambiguous.  Without an
    exact match, the ambiguity error names what matched (first two +
    count) so the provenance is troubleshooting-useful."""

    @pytest.mark.asyncio
    async def test_exact_match_wins_over_annexe_variant(self):
        from houses.council_tax import lookup_council_tax

        class Row:
            def __init__(self, address: str, band: str):
                self.address = address
                self.band = band
                self.local_authority = "Woking"

        class Page:
            rows = [
                Row("2 WILLOWMEAD GARDENS", "D"),
                Row("FLAT 2, 2 WILLOWMEAD GARDENS", "A"),
            ]

        def fetcher(postcode: str, page: int):
            return Page()

        a = await lookup_council_tax(
            "SL7 1HW",
            "2 Willowmead Gardens, Marlow, SL7 1HW",
            page_fetcher=fetcher,
        )
        assert a.succeeded, f"exact address must win, got: {a.status}: {a.error}"
        info = a.value_or_none()
        assert info is not None
        assert info.band == "D"

    @pytest.mark.asyncio
    async def test_no_exact_match_stays_ambiguous_with_names(self):
        from houses.council_tax import lookup_council_tax

        class Row:
            def __init__(self, address: str, band: str):
                self.address = address
                self.band = band
                self.local_authority = "Woking"

        class Page:
            rows = [
                Row("2 WILLOWMEAD GARDENS", "D"),
                Row("2 WILLOWMEAD COURT", "D"),
            ]

        def fetcher(postcode: str, page: int):
            return Page()

        # "2 Willowmead" alone names the number but no building — neither
        # row is an exact prefix of the query, so it stays ambiguous.
        a = await lookup_council_tax(
            "SL7 1HW",
            "2 Willowmead, Marlow, SL7 1HW",
            page_fetcher=fetcher,
        )
        assert a.impossible
        assert "address matched multiple properties" in (a.error or "")
        assert "'2 WILLOWMEAD GARDENS'" in (a.error or "")
        assert "'2 WILLOWMEAD COURT'" in (a.error or "")
        assert "(2 matches)" in (a.error or "")


class TestAnnexeDetection:
    """An annexe is a single extra VOA property whose address is the main
    address with a unit prefix — 'FLAT 2, 2 WILLOWMEAD GARDENS' contains
    '2 WILLOWMEAD GARDENS'.  Locality-suffixed duplicates are the same
    property, not an annexe."""

    @pytest.mark.asyncio
    async def test_unit_prefixed_row_is_detected_as_annexe(self):
        from houses.council_tax import lookup_council_tax

        class Row:
            def __init__(self, address: str, band: str):
                self.address = address
                self.band = band
                self.local_authority = "Woking"

        class Page:
            rows = [
                Row("2 WILLOWMEAD GARDENS", "D"),
                Row("FLAT 2, 2 WILLOWMEAD GARDENS", "A"),
            ]

        def fetcher(postcode: str, page: int):
            return Page()

        a = await lookup_council_tax(
            "SL7 1HW",
            "2 Willowmead Gardens, Marlow, SL7 1HW",
            page_fetcher=fetcher,
        )
        assert a.succeeded
        info = a.value_or_none()
        assert info is not None and info.annexe is not None
        assert info.annexe.address == "FLAT 2, 2 WILLOWMEAD GARDENS"
        assert info.annexe.band == "A"
        assert info.annexe.yearly_cost is not None

    @pytest.mark.asyncio
    async def test_locality_suffixed_duplicate_is_not_annexe(self):
        from houses.council_tax import lookup_council_tax

        class Row:
            def __init__(self, address: str, band: str):
                self.address = address
                self.band = band
                self.local_authority = "Woking"

        class Page:
            rows = [
                Row("2 WILLOWMEAD GARDENS", "D"),
                Row("2 WILLOWMEAD GARDENS MARLOW", "D"),
            ]

        def fetcher(postcode: str, page: int):
            return Page()

        a = await lookup_council_tax(
            "SL7 1HW",
            "2 Willowmead Gardens, Marlow, SL7 1HW",
            page_fetcher=fetcher,
        )
        assert a.succeeded
        info = a.value_or_none()
        assert info is not None
        assert info.annexe is None, "locality-suffixed row is the same property"

    @pytest.mark.asyncio
    async def test_two_prefixed_rows_means_no_annexe(self):
        from houses.council_tax import lookup_council_tax

        class Row:
            def __init__(self, address: str, band: str):
                self.address = address
                self.band = band
                self.local_authority = "Woking"

        class Page:
            rows = [
                Row("2 WILLOWMEAD GARDENS", "D"),
                Row("FLAT 1, 2 WILLOWMEAD GARDENS", "A"),
                Row("FLAT 2, 2 WILLOWMEAD GARDENS", "B"),
            ]

        def fetcher(postcode: str, page: int):
            return Page()

        a = await lookup_council_tax(
            "SL7 1HW",
            "2 Willowmead Gardens, Marlow, SL7 1HW",
            page_fetcher=fetcher,
        )
        assert a.succeeded
        info = a.value_or_none()
        assert info is not None
        assert info.annexe is None, "two prefixed rows are flats, not a single annexe"


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

    def test_legacy_district_aliases_resolve_to_unitary_rate(self):
        """VOA rows carry the LEGACY district name (Wycombe), but the CSV
        is keyed by today's billing authority (buckinghamshire ua — the
        2020 unitary reorg).  Without the alias the yearly cost is None
        and the council tax contributes £0 despite a real band."""
        from houses.council_tax import _lookup_yearly_cost

        cost = _lookup_yearly_cost("F", "Wycombe")
        assert cost is not None, "Wycombe must alias to the Buckinghamshire UA rate"
        assert cost.amount > 0


class TestRealAddressForm:
    """Real property addresses carry the county ('2 Willowmead Gardens,
    Marlow, Buckinghamshire, SL7 1HW') while VOA rows carry only the
    locality ('2 WILLOWMEAD GARDENS, MARLOW, SL7 1HW').  The exact-match
    rule must survive the county token."""

    @pytest.mark.asyncio
    async def test_exact_match_survives_county_and_detects_annexe(self):
        from houses.council_tax import lookup_council_tax

        class Row:
            def __init__(self, address: str, band: str):
                self.address = address
                self.band = band
                self.local_authority = "Wycombe"

        class Page:
            rows = [
                Row("2 WILLOWMEAD GARDENS, MARLOW, SL7 1HW", "F"),
                Row("FLAT 2, 2 WILLOWMEAD GARDENS, MARLOW, SL7 1HW", "A"),
                Row("12 WILLOWMEAD GARDENS, MARLOW, SL7 1HW", "D"),
            ]

        def fetcher(postcode: str, page: int):
            return Page()

        a = await lookup_council_tax(
            "SL7 1HW",
            "2 Willowmead Gardens, Marlow, Buckinghamshire, SL7 1HW",
            page_fetcher=fetcher,
        )
        assert a.succeeded, f"exact match must win despite the county, got: {a.status}: {a.error}"
        info = a.value_or_none()
        assert info is not None
        assert info.band == "F"
        assert info.annexe is not None
        assert info.annexe.address == "FLAT 2, 2 WILLOWMEAD GARDENS, MARLOW, SL7 1HW"
        assert info.annexe.band == "A"


class TestUnitPrefixedAnnexe:
    """The classic UK annexe form '2A WILLOWMEAD GARDENS' (main house
    number + a letter) is a superset of '2 WILLOWMEAD GARDENS' and must
    be detected — while '12'/'20' (digit-prefixed) must not be."""

    @pytest.mark.asyncio
    async def test_letter_suffixed_number_is_detected_as_annexe(self):
        from houses.council_tax import lookup_council_tax

        class Row:
            def __init__(self, address: str, band: str):
                self.address = address
                self.band = band
                self.local_authority = "Woking"

        class Page:
            rows = [
                Row("2 WILLOWMEAD GARDENS, MARLOW, SL7 1HW", "F"),
                Row("2A WILLOWMEAD GARDENS, MARLOW, SL7 1HW", "A"),
                Row("2AB WILLOWMEAD GARDENS, MARLOW, SL7 1HW", "B"),
                Row("12 WILLOWMEAD GARDENS, MARLOW, SL7 1HW", "D"),
                Row("20 WILLOWMEAD GARDENS, MARLOW, SL7 1HW", "D"),
            ]

        def fetcher(postcode: str, page: int):
            return Page()

        a = await lookup_council_tax(
            "SL7 1HW",
            "2 Willowmead Gardens, Marlow, SL7 1HW",
            page_fetcher=fetcher,
        )
        assert a.succeeded, f"lookup must succeed, got: {a.status}: {a.error}"
        info = a.value_or_none()
        assert info is not None
        assert info.band == "F"
        assert info.annexe is not None, "2A is the annexe — a letter-suffixed number"
        assert "2A WILLOWMEAD" in info.annexe.address
        assert info.annexe.band == "A"
        # A multi-char suffix ("2AB") is NOT the single-letter form — the
        # len(row)+1 guard excludes it (the review's concern was moot).
        assert "2AB" not in info.annexe.address


class TestMissingRate:
    """A matched band whose local authority has no resolvable yearly rate
    must still succeed (the band is real) but record WHY there is no
    cost — the provenance must not silently show a band with no figure
    and no explanation."""

    @pytest.mark.asyncio
    async def test_matched_band_without_rate_records_lookup_error(self):
        from houses.council_tax import lookup_council_tax

        class Row:
            def __init__(self, address: str, band: str):
                self.address = address
                self.band = band
                self.local_authority = "Narnia"

        class Page:
            rows = [Row("2 WILLOWMEAD GARDENS, MARLOW, SL7 1HW", "F")]

        def fetcher(postcode: str, page: int):
            return Page()

        a = await lookup_council_tax(
            "SL7 1HW",
            "2 Willowmead Gardens, Marlow, SL7 1HW",
            page_fetcher=fetcher,
            rate_lookup=lambda band, la: None,
        )
        assert a.succeeded, f"band match must succeed even without a rate: {a.status}: {a.error}"
        info = a.value_or_none()
        assert info is not None
        assert info.band == "F"
        assert info.yearly_cost is None
        assert "Narnia" in (info.lookup_error or ""), (
            "the provenance must say WHICH authority has no rate, got: "
            f"{info.lookup_error!r}"
        )

    def test_provenance_value_includes_lookup_error(self):
        from houses.council_tax_info import CouncilTaxInfo

        info = CouncilTaxInfo(band="F", lookup_error="no yearly rate found for Narnia")
        assert "no yearly rate found for Narnia" in info.to_provenance_value()
        assert "Band F" in info.to_provenance_value()

    @pytest.mark.asyncio
    async def test_evidence_url_points_at_working_civaccount_api(self):
        """The old evidence URL (civaccount.co.uk/councils/<slug>) 404s for
        EVERY authority — the CivAccount website has no such pages.  The
        API endpoint that actually serves the rate must be the link."""
        from houses.council_tax import lookup_council_tax

        class Row:
            def __init__(self, address: str, band: str):
                self.address = address
                self.band = band
                self.local_authority = "Woking"

        class Page:
            rows = [Row("2 WILLOWMEAD GARDENS, MARLOW, SL7 1HW", "F")]

        def fetcher(postcode: str, page: int):
            return Page()

        a = await lookup_council_tax(
            "SL7 1HW",
            "2 Willowmead Gardens, Marlow, SL7 1HW",
            page_fetcher=fetcher,
            rate_lookup=lambda band, la: None,
        )
        info = a.value_or_none()
        assert info is not None
        assert info.evidence_url.startswith("https://www.civaccount.co.uk/api/v1/councils/"), (
            "evidence must link to the working API endpoint, got: "
            f"{info.evidence_url!r}"
        )
        assert "woking" in info.evidence_url
