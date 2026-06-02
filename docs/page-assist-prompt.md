Extract structured data from the page content and output a curl command.

**How to find each field:**

- `address`: The property address line as shown near the top of the listing.
  Usually "High Street, Some Town, RG14 1AA" or just "Somewhere Road, Town Name".
  A house number or full postcode may not be present — that's fine.
- `postcode`: The full UK postcode if visible anywhere on the page. Look for
  "Postcode:" in the details section, or a full postcode (e.g. "RG14 1AA",
  "SL6 2AA") at the end of the address line. If only a partial postcode is
  visible (e.g. "SL6"), extract it anyway but also leave it in the address.
  If no postcode is found at all, omit this field.
- `bedrooms`: Number of bedrooms — "2 bedrooms", "3 bed". Optional — omit if unclear.
- `price`: The listing price as a number (strip £ and commas). "£650,000" → 650000.
  Optional — omit if not clearly shown.

**Output curl command, nothing else:**

Then, curl command:
```bash
curl -X POST http://127.0.0.1:8080/inject-property \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.rightmove.co.uk/properties/xxxxxx","address":"High Street, Some Town, RG14 1AA","postcode":"RG14 1AA","bedrooms":3,"price":650000}'
```

**Validation:**
- `bedrooms` if provided must be a positive integer 1-10
- `price` if provided must be a positive number >= 400000 and < 1000000
