# Schools

**Module**: `houses/schools.py` — `find_nearest()`, `compute_school_commute()`

**Purpose**: Find the nearest primary and secondary schools that accept boys and are non-fee-paying.

**Data source:** `data/edubaseall_enriched.csv` — GIAS "All establishments" CSV enriched with Ofsted ratings and coordinates.

**How it works (`find_nearest`):**
1. Loads school CSV, filters by phase (primary/secondary), gender (mixed or boys), fee status (non-fee-paying).
2. Geocodes property postcode via postcodes.io (ORS Pelias as fallback for addresses).
3. Computes Haversine distance to each eligible school within configured search radius (default: 5 km).
4. Returns the nearest qualifying school.

**School constraints:**
- Must accept boys (gender = mixed or boys)
- Must be non-fee-paying (not "independent school", "other independent school", etc.)
- Must match target phase ("primary" or "secondary")
- If closest secondary is girls-only, substitutes nearest mixed/boys alternative

**School commute** (`compute_school_commute`): Delegates to `routing.get_commute(has_car=False, max_walk_minutes=20)` for walking/transit time.

**Columns populated:** Primary/Secondary School, Distance (km), Walk (min), School Link, Ofsted, Inspection Year, Secondary Bus (min), Secondary Bus Route

**Graceful degradation:** Returns `None` when CSV is missing, geocoding fails, or no eligible school is within range.
