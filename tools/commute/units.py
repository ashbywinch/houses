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
KM = 1.0 * ureg.km
MINUTE = 1.0 * ureg.minute
KMH = 1.0 * (ureg.km / ureg.hour)  # kilometre per hour (speed)
