# Council Tax

**Module**: `houses/council_tax.py` — `lookup_council_tax()`

**Purpose**: Look up council tax band and yearly cost by scraping the public VOA website.

**How it works:**
1. Scrapes `https://www.tax.service.gov.uk/check-council-tax-band/search` via `uk-property-apis` VOAClient.
2. Extracts CSRF token, POSTs postcode, parses HTML results table.
3. Matches specific property by building name/number from the full address.
4. Extracts band and local authority from the VOA result.
5. Looks up Band D rate from CivAccount API, falling back to `data/council_tax_rates.csv`.
6. Applies band ratio to compute specific yearly cost.

**Band ratios:** A=6/9, B=7/9, C=8/9, D=9/9, E=11/9, F=13/9, G=15/9, H=18/9.

**Coverage:** England and Wales only. Scottish postcodes return no results.

**Columns populated:** Council Tax Band, Council Tax Cost (£)

**Graceful degradation:** Returns `Attempt.impossible(...)` when:
- `uk-property-apis` not installed
- No address provided or no building identifier extractable
- VOA returns no results or no match found (ambiguous addresses fail explicitly)
- Any HTTP or parsing error
