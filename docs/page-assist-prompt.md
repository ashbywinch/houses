Extract structured data from the page content and output a curl command.

**How to find each field:**

- `address`: The property address as shown near the top of the listing.
  Usually in the form "High Street, Some Town, RG14 1AA" or just
  "Somewhere Road, Town Name". A house number or full postcode may not
  be present — that's fine, extract what you can see.
- `bedrooms`: Look for "2 bedrooms", "3 bed", or similar. Optional —
  if not visible on the page, omit 
- `price`: The listing price as a number (strip £ and commas).
  "£650,000" → 650000. Optional — omit if not clearly shown.

**Output curl command, nothing else:**

Then, curl command:
```bash
curl -X POST http://127.0.0.1:8080/inject-property \
  -H "Content-Type: application/json" \
  -d '{"address":"High Street, Some Town, RG14","bedrooms":3,"price":650000, "url":"https://www.rightmove.co.uk/properties/xxxxxx"}'
```

**Validation:**
- `bedrooms` if provided must be a positive integer 1-10
- `price` if provided must be a positive number >= 400000 and < 1000000
