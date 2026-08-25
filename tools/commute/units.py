"""Unit constants for the commute toolchain (coding-standards: idiomatic pint).

Numeric literals become quantities by MULTIPLYING by a unit constant
(``4.0 * KM``), never by calling the ``Quantity`` constructor with a literal
unit string. One registry per package — pint forbids mixing quantities from
different registries.
"""

from pint import UnitRegistry

ureg = UnitRegistry()

# one unit expressed as a Quantity (not a bare Unit): the pint stubs type
# ``scalar * Unit`` as an unhelpful union, but ``scalar * Quantity`` is
# cleanly a Quantity — and ``4.0 * KM`` is exactly 4 km either way.
KM = 1.0 * ureg.km  # type: ignore[unsupported-operation]  # coding-standards.md mandates this Quantity-constant idiom (`1.0 * ureg.km`), but pint's stubs type `scalar * Unit` as a union rather than Quantity — a genuine stub limitation, not a code smell
MINUTE = 1.0 * ureg.minute  # type: ignore[unsupported-operation]  # same mandated Quantity-constant idiom; pint stub limitation as above
KMH = 1.0 * (ureg.km / ureg.hour)  # type: ignore[unsupported-operation]  # kilometre per hour (speed); Unit/Unit division is also untyped in pint's stubs — same mandated idiom
