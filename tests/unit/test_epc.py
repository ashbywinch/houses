"""EPC certificate matching — a building NUMBER must match as a whole
token, never a substring: "2" must not claim "12"/"20"/"A2" Willowmead
Gardens (the same hardening as the council-tax lookup). Regression:
"address matched multiple properties" for a plain numbered house."""
from __future__ import annotations

from houses.epc import _match_cert


def _cert(address: str, band: str = "C") -> dict:
    return {"addressLine1": address, "currentEnergyEfficiencyBand": band, "registrationDate": "2025-01-01"}


def test_numbered_building_matches_only_that_number():
    certs = [
        _cert("2 WILLOWMEAD GARDENS", band="B"),
        _cert("12 WILLOWMEAD GARDENS"),
        _cert("20 WILLOWMEAD GARDENS"),
        _cert("21 WILLOWMEAD GARDENS"),
    ]
    a = _match_cert(certs, "2", "2 Willowmead Gardens, Marlow, SL7 1HW")
    assert a.succeeded, f"2 must match only its own certificate, got: {a.status}: {a.error}"
    assert a.value_or_none() == "B"


def test_number_does_not_match_letter_or_digit_prefixed_substrings():
    """A2/12A must not be claimed by the bare number."""
    certs = [
        _cert("2 WILLOWMEAD GARDENS"),
        _cert("12A WILLOWMEAD GARDENS"),
        _cert("A2 WILLOWMEAD GARDENS"),
    ]
    a = _match_cert(certs, "2", address="2 Willowmead Gardens, Marlow, SL7 1HW")
    assert a.succeeded, f"got: {a.status}: {a.error}"
    assert a.value_or_none() == "C"


def test_street_pairing_disambiguates_flats_from_house():
    """The street token after the number is required — the house row and
    the flat's row both contain the token '2', so they stay ambiguous,
    but '2' alone never grabs '20'."""
    certs = [
        _cert("2 WILLOWMEAD GARDENS"),
        _cert("FLAT 2, 2 WILLOWMEAD GARDENS"),
        _cert("20 WILLOWMEAD GARDENS"),
    ]
    a = _match_cert(certs, "2", address="2 Willowmead Gardens, Marlow, SL7 1HW")
    # House + flat rows are genuinely distinct addresses — ambiguous is
    # correct; the bug would have been '20' joining the set too.  The
    # error names what matched so the provenance is troubleshooting-useful.
    assert a.impossible
    assert "multiple properties" in (a.error or "")
    assert "'2 WILLOWMEAD GARDENS'" in (a.error or "")
    assert "'FLAT 2, 2 WILLOWMEAD GARDENS'" in (a.error or "")
    assert "(2 matches)" in (a.error or "")
    assert "20 WILLOWMEAD" not in (a.error or "")

def test_no_match_reports_no_certificate():
    certs = [_cert("12 WILLOWMEAD GARDENS")]
    a = _match_cert(certs, "2", address="2 Willowmead Gardens, Marlow, SL7 1HW")
    assert a.impossible
    assert "no matching certificate" in (a.error or "")
