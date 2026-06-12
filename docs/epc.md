# EPC Rating

**Module**: `houses/epc.py` — `lookup_epc()`

**Purpose**: Look up the Energy Performance Certificate band for a property via the UK Government Energy Performance Data API.

**API:** `https://api.get-energy-performance-data.communities.gov.uk/api/domestic/search`
**Auth:** Bearer token (register at the API docs page). Configured via `epc_bearer_token`.

**How it works:**
1. Validates the address — skips if the first token is a road name without a number (ambiguous).
2. Extracts building identifier (number or name) from the first address token.
3. Calls the government EPC API with the postcode (up to 50 results per request).
4. When address is provided, filters certificates to match the building identifier.
5. Ambiguity check: if multiple distinct addresses match, returns empty (no guessing).
6. Returns the `currentEnergyEfficiencyBand` (A–G) from the most recent certificate.

**Columns populated:** EPC Rating

**Graceful degradation:**
- Returns empty string if `epc_bearer_token` not configured.
- Returns empty string if address is ambiguous or no certificate matches.
- Returns empty string on any API failure.
