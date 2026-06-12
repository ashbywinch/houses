# Transit Commute (Simon / Lorena)

**Primary module**: `houses/routing.py` — `get_commute()`
**Coordinators**: `houses/enricher.py` — `compute_simon_commute()`, `compute_lorena_commute()`
**Value objects**: `houses/commute.py` — `Commute`, `CostGroup`, `JourneyLeg`, `LegMode`
**TfL integration**: `houses/transit_route.py` — `TransitRoute`
**Bus fares**: `houses/bus_journey.py` — `BusJourneyRegistry`, `cheapest_round_trip()`

**Purpose**: Calculate door-to-door public transport commute times and costs from a property to Simon's work (SW1V 2QQ) and Lorena's work (EC3A 7LP).

**How it works (`get_commute`):**
1. Checks congestion zone — skips driving for central London destinations.
2. Tries walking via Google Routes WALK mode (only if ≤ max_walk_minutes).
3. Tries transit via **Google Routes TRANSIT mode first** (covers all UK bus/rail).
4. Falls back to **TfL Journey Planner** for London-area destinations when Google lacks fare data.
5. If traveler `has_car`, tries driving via ORS Directions.
6. Picks the fastest route among available options, preferring priced routes.

**Simon commute** (`compute_simon_commute`): `has_car=True, max_walk_minutes=15` — park-and-ride is enabled.
**Lorena commute** (`compute_lorena_commute`): `has_car=False, max_walk_minutes=30` — transit only.

**Bus fares**: Google Routes bus legs are matched against BODS zone data via `BusJourneyRegistry` for cost estimates. TfL bus legs also fall back to BODS when TfL doesn't price them.

**Route summary**: Each commute builds `CostGroup`/`JourneyLeg` objects for a human-readable route summary (e.g. `walk 5m → Train to Paddington (18m) → Bakerloo line to Oxford Circus (8m)`).

**Columns populated:**
- Simon: Simon London (min), Simon London Cost (£), Simon London Route, Simon Parking Cost (£)
- Lorena: Lorena London (min), Lorena London Cost (£), Lorena London Route

**Graceful degradation:** Returns `Attempt.impossible(...)` when routing fails — caller decides how to handle missing data. Walking-only results are returned when transit APIs fail.
