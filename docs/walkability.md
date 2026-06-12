# Walkability

**Module**: `houses/walkability.py` — `enrich_walkability()`

**Purpose**: Assess how walkable the area is — distance to town centre and nearby amenities.

**How it works:**
1. Extracts town name from property address (filters out postcodes and counties).
2. Geocodes town centre via ORS Pelias, then computes ORS foot-walking duration.
3. Google Maps Places API (New) Nearby Search: searches for supermarket, park, pharmacy, convenience_store within 1000m radius.
4. Falls back to OpenStreetMap Overpass API if Google Places fails.
5. Formats amenities as `"Maidenhead | Sainsbury's | Waitrose & Partners"`.

**API costs:** Google Places: $200/month free credit (~5,000 calls). Overpass: free, no key needed.

**Columns populated:** Walk to Town (min), Walkable Amenities

**Graceful degradation:**
- Google Places missing/unavailable → skip amenities search, still compute walk time
- ORS geocoding fails → fall back to Haversine approximation
- No amenities found → empty amenities string
