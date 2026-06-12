# Council Tax

Looks up council tax band and yearly cost. Requires a full property
address with building number/name — ambiguous addresses fail explicitly
(no guessing). England and Wales only (Scottish postcodes return nothing).

- **Module**: `houses/council_tax.py` → `lookup_council_tax()`
- **Dependency**: `uk-property-apis` (MIT, GitHub)
