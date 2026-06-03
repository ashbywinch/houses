# Enrichment Modules

Each enrichment module transforms a raw property payload into structured data. All modules are designed to fail gracefully — if an API is unavailable or misconfigured, they log a warning and return `None` or empty defaults.

## Transit Commute (TfL Unified API)

**Module**: `houses/enricher.py` — `compute_transit()`

**Purpose**: Calculate door-to-door public transport commute times and fares from a property to Simon's work (SW1V 2QQ) and Lorena's work (EC3A 7LP).

**How it works:**
1. Calls TfL Journey Planner: `GET https://api.tfl.gov.uk/Journey/JourneyResults/{origin}/to/{destination}`
2. Extracts `journeys[0].duration` (minutes) — door-to-door including walking
3. Extracts `journeys[0].fare.totalCost` (pence, converted to GBP)
4. Daily cost = 2 × single fare (return trip)
5. If TfL returns 300 (Multiple Choices), falls back to geocoding the origin via ORS/Google Maps and retrying with coordinates
6. If TfL returns no fare data, falls back to National Rail fares lookup

**Limitations:**
- TfL covers Greater London and surrounding areas. Properties outside this range return `None`.
- Fare is a conservative overestimate (2× single fare ignores daily caps).

**Graceful degradation:** Returns `TransitInfo` with `duration_minutes=None` when:
- API key not configured
- TfL returns 404 (route outside coverage area)
- Any HTTP or parsing error

## Petrol Cost (OpenRouteService)

**Module**: `houses/enricher.py` — `compute_petrol_cost()`

**Purpose**: Estimate the daily petrol cost for driving to the Bracknell office (RG12 8YA).

**How it works:**
1. Geocodes property postcode via postcodes.io (most reliable for UK outcodes)
2. Falls back to ORS Pelias or Google Maps Geocoding (if configured)
3. Requests driving-car route from property → Bracknell
4. Extracts `routes[0].summary.distance` (one-way km, then ×2 for round trip)
5. Extracts `routes[0].summary.duration` (seconds, then ×2 / 60 for round-trip minutes)
6. Computes petrol cost: `(round_trip_km / 100) × (235.214 / mpg) × price_per_litre`

**Formula:**
```
litres_per_100km = 235.214 / petrol_mpg       # default: 45 mpg
litres_used = (round_trip_km / 100) × litres_per_100km
cost = litres_used × petrol_price_per_litre    # default: £1.45/L
```

**Default constants** (configurable in `config.py`):
- 45 mpg fuel efficiency
- £1.45/L petrol price
- Route is one-way; distance and duration are doubled for round trip

**Important**: The property postcode is passed directly to `compute_petrol_cost`, not the full address. This ensures postcodes.io can geocode it reliably (outcodes like SL7 geocode correctly).

**Graceful degradation:** Returns `PetrolCost()` with `cost_gbp=None` when geocoding or routing fails.

## Rail Fare Fallback (National Rail DTD Data)

**Module**: `houses/rail_fares.py` — `nearest_station()`, `fare_between()`

**Purpose**: When TfL doesn't return fare data for a route, estimate the cost using National Rail fares.

**Data source:** `data/rail_fares.csv` — a filtered subset of the NR DTD fares feed, extracted by `scripts/extract_rail_fares.py`. Contains single-equivalent fares from any UK station to all London terminals.

**How it works:**
1. `nearest_station(lat, lng)` — finds the nearest railway station using Haversine distance (zero API calls, 2,605 stations from `data/stations.csv`)
2. `fare_between(origin_crs, dest_crs)` — looks up the cheapest single-equivalent fare from the CSV
3. If destination is a London terminal (VIC, FST, PAD, etc.), tries all London terminals and picks the cheapest
4. A zone 1 tube continuation cost (£2.80 per single) is added when using rail fares

**Data generation:**
- `scripts/extract_rail_fares.py` — scans the NR DTD FFL file for fares to London terminals, extracts ticket records, filters to peak-valid tickets only
- Two-pass scan of a 253MB FFL file: first for flow records (814K), then for ticket records (8.8M)
- About 261 Anytime Return (OR2) fares extracted to London Terminals

**Peak-time detection:** A fare is valid at peak times iff its RESTRICTION_CODE (positions 20–21 of the RT record) is empty. The ticket type name alone (e.g. "SDS") does not determine peak validity — an SDS with no restriction code is valid anytime. The extractor filters to unrestricted fares only, yielding ~2,851 peak-valid fares covering most London-commutable stations.

**Graceful degradation:** Returns `None` when the CSV doesn't contain a fare for the station, allowing the "?" to display honestly.

## Schools (GIAS CSV + postcodes.io)

**Module**: `houses/enricher.py` — `find_nearest_boys_primary()`, `find_nearest_boys_secondary()`

**Purpose**: Find the nearest primary and secondary schools that accept boys and are non-fee-paying.

**Data source**: GIAS "All establishments" CSV (gov.uk), enriched with Ofsted ratings and coordinates.

**How it works:**
1. Loads school CSV into memory
2. Filters by phase (primary/secondary), gender (mixed or boys), fee status (non-fee-paying)
3. Geocodes property postcode via postcodes.io (ORS Pelias as fallback for addresses)
4. Computes Haversine distance to each eligible school
5. Selects the nearest school within the configured search radius (default: 5 km)
6. If the closest secondary is girls-only, substitutes the nearest mixed/boys alternative
7. Returns `SchoolInfo` with name, distance, walking time, bus time (20 km/h estimate), Ofsted rating

**School data files:**
- `data/edubaseall_enriched.csv` — enriched with coordinates + Ofsted
- `data/edubaseall_full.csv` — full GIAS download (source for merge)
- `data/ofsted_inspections.csv` — Ofsted management information CSV (state-funded schools only, ~60% coverage)

**Ofsted data limitation:** Only ~60% of schools have Ofsted ratings because the management information CSV only covers state-funded schools with recent inspections. Schools without recent inspections show empty Ofsted.

**School constraints:**
- Must accept boys (gender = "mixed" or "boys")
- Must be non-fee-paying (not "independent school", "other independent school", etc.)
- Must match target phase ("primary" or "secondary" in phase name)

**Graceful degradation:** Returns `None` when CSV is missing, property can't be geocoded, or no eligible school is within range.

## Walkability (Google Maps Places + ORS)

**Module**: `houses/walkability.py` — `enrich_walkability()`

**Purpose**: Assess how walkable the area is — distance to town centre, nearby amenities.

**How it works:**
1. Extracts town name from property address
2. Geocodes town centre via ORS Pelias
3. ORS foot-walking directions: calculates walk time to town centre
4. Google Maps Places API (New) Nearby Search: `POST https://places.googleapis.com/v1/places:searchNearby`
   - Types: supermarket, park, pharmacy, train_station, convenience_store
   - Radius: 1000m
   - Field mask: `places.displayName,places.types`
   - **Note**: `places.distanceMeters` is NOT a valid field mask in the New Places API — omitting it
5. Formats amenities as: `"Maidenhead | Sainsbury's | Waitrose & Partners"`

**API costs:**
- Google Maps Places API (New): $200 monthly free credit (~5,000 calls at $0.04/request)
- Rate limit: ~600 requests/minute. 429 errors mean per-minute quota exhausted; resets within 1–60 seconds.
- Monthly quota resets on billing date.
- ORS: included in existing API key usage

**Graceful degradation:**
- If Google Maps key missing or API fails (429, 400, etc.): skip amenities search, still compute walk time
- If ORS geocoding fails for town centre: fall back to Haversine approximation
- If no amenities found: return empty amenities string

**Current limitation:** The Places API (New) `searchNearby` endpoint has a known issue: `places.distanceMeters` is not a valid field mask parameter. Use only `places.displayName,places.types` in the `X-Goog-FieldMask` header.

## Town Description (LLM / OpenRouter)

**Module**: `houses/town_desc.py` — `generate_town_description()`

**Purpose**: Generate a single-sentence, honest description of a neighbourhood for someone choosing where to buy a home.

**How it works:**
1. Calls OpenRouter chat completions: `POST https://openrouter.ai/api/v1/chat/completions`
2. System prompt instructs the model to be specific and balanced (mentions trade-offs, avoids marketing fluff)
3. Output is truncated to the first sentence (safety measure against multi-town leakage)
4. Results cached in-memory by town name
5. Configurable via settings: `llm_model`, `llm_temperature`, `llm_max_tokens`

**Prompt rules:**
- Exactly one sentence, no markdown
- Honest about trade-offs (lively vs quiet, polished vs gritty)
- No mention of house prices, transport, commute times, or schools (separate columns)
- No repeating the area name

**Graceful degradation:**
- Returns empty string if no API key configured
- Logs warning and returns "" on any API failure

## Council Tax (Homedata + CivAccount)

**Module**: `houses/council_tax.py` — `lookup_council_tax()`

**Purpose**: Look up council tax band and yearly cost for a property.

**API Details:**
- Base URL: `https://homedata.co.uk/api` (NOT `api.homedata.co.uk`)
- Auth: `Authorization: Api-Key {key}`
- Endpoint: `GET /council_tax_band/` — accepts `uprn`, or `postcode` + `building_number`/`building_name`
- Also: `GET /property/{uprn}/core` — newer endpoint (May 2026) that includes council tax + EPC + flood risk + schools + broadband in one call

**How it works:**
1. Extracts building name/number from the property address
2. Calls Homedata: `GET /council_tax_band/?postcode={pc}&building_name={name}` with `Api-Key` auth
3. Parses council tax band and local authority from response
4. Derives council slug from local authority name
5. Calls CivAccount (free, no auth): `GET https://www.civaccount.co.uk/api/v1/councils/{slug}`
6. Applies band ratio to Band D rate for specific band cost
7. Caches results in-memory per postcode

**Current limitation:** The Homedata API returns 404 for all postcode/UPRN lookups in the free tier. The address search endpoint works (returns suggestions with UPRNs) but the property data endpoints return 404. This may require:
- Upgrading from the free tier (100 calls/month) to Starter (£49/mo)
- Waiting for the API migration to the new `/property/{uprn}/` system (May 2026)
- Checking that the API key has been activated for property data access

**Council tax band ratios:**
| Band | Ratio |
|------|-------|
| A | 6/9 |
| B | 7/9 |
| C | 8/9 |
| D | 9/9 |
| E | 11/9 |
| F | 13/9 |
| G | 15/9 |
| H | 18/9 |

**Graceful degradation:**
- Returns `None` if API key not configured
- Returns `None` if property not found (404)
- Returns `None` on any API failure or auth error (403)

## Commute Breakdown (Yearly Cost Calculation)

**Module**: `houses/enricher.py` — `compute_commute_breakdown()`

**Purpose**: Combine individual commute costs into a single yearly total.

**How it works:**
1. Reads daily costs from TransitInfo for Simon and Lorena
2. Reads daily petrol cost for Bracknell
3. Calculates yearly total:

```
yearly_total = working_weeks × (bracknell_daily_cost + simon_daily_cost + lorena_daily_cost × weekly_lorena_trips)
```

With default values:
```
yearly_total = 46 × (1 × Bracknell_daily + 1 × Simon_daily + 2 × Lorena_daily)
```

**Returns:** `CommuteBreakdown` with individual daily costs, yearly total, and a human-readable formula string.

## School Bus Time

**Module**: `houses/enricher.py` — `_school_to_info()` computes `bus_time_minutes`

**Purpose**: Estimate the bus travel time to secondary school as an alternative to walking. The View tab has a dedicated "Secondary Bus (min)" column.

**How it works:**
- Computed from Haversine distance at 20 km/h average bus speed (includes stops and traffic)
- Stored alongside `walking_time_minutes` on the `SchoolInfo` model
- Displayed in the View tab via XLOOKUP from Data!AC

## EPC Rating — Not Yet Implemented

**Column**: Properties Data (AB), Properties View (D)

**Status**: Placeholder column awaiting integration with an EPC data source.

**Potential approach:** EPC data is available from:
- **Homedata** via `/property/{uprn}/core` includes `current_energy_rating` and `potential_energy_rating`
- **Open EPCR** — free EPC API with rate limits
- **EPC Register** — gov.uk bulk data downloads

The Homedata approach would be the most natural, since it shares the same API key and data source as council tax. The flow would be: address search → UPRN → `/property/{uprn}/core` for EPC + council tax in one call.
