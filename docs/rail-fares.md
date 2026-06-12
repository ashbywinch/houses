# Rail Fare Fallback

**Module**: `houses/rail_fares.py` — `nearest_station()`, `fare_between()`

**Purpose**: When TfL doesn't return fare data for a route, estimate the cost using National Rail fares from a local CSV.

**Data source:** `data/rail_fares.csv` — a filtered subset of the NR DTD fares feed, extracted by `scripts/extract_rail_fares.py`. Contains single-equivalent fares from UK stations to London terminals.

**How it works:**
1. `nearest_station(lat, lng)` — finds the nearest railway station from `data/stations.csv` using Haversine distance (2,605 stations, zero API calls).
2. `fare_between(origin_crs, dest_crs)` — looks up cheapest single-equivalent fare from the CSV, trying both direction orders.
3. If destination is a London terminal (VIC, FST, PAD, etc.), tries all 15 London terminals and picks the cheapest.
4. A zone 1 tube continuation cost (£2.80 per single) is added when using rail fares.

**Peak-time filtering:** Only unrestricted fares (no restriction code) are included, yielding ~2,851 peak-valid fares.

**Graceful degradation:** Returns `None` when the CSV doesn't contain a fare — the sheet displays "?" honestly.
