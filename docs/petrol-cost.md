# Petrol Cost (Bracknell)

**Module**: `houses/enricher.py` — `compute_petrol_cost()`
**Delegates to**: `houses/routing.py` — `_drive_commute()`

**Purpose**: Estimate the daily petrol cost for driving to the Bracknell office (RG12 8YA).

**How it works:**
1. Geocodes property postcode via postcodes.io (most reliable for UK outcodes), falling back to ORS Pelias or Google Maps Geocoding.
2. Requests `driving-car` route from property → Bracknell via OpenRouteService Directions API.
3. Extracts one-way distance (km) and doubles for round trip.
4. Extracts one-way duration (seconds) and doubles for round-trip minutes.
5. Computes petrol cost: `(round_trip_km / 100) × (235.214 / mpg) × price_per_litre`

**Default constants** (configurable via `config.py`): 45 mpg, £1.45/L petrol price.

**Columns populated:** Bracknell Time (min), Bracknell Cost (£)

**Graceful degradation:** Returns `Attempt.impossible(...)` when geocoding or routing fails. The sheet displays empty cells for missing values.
