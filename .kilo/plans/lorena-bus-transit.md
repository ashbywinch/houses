# Plan: Commute Route Options — Lorena Bus, Simon Parking

## Goal

1. **Lorena**: Allow bus use on transit commute when it saves significant time (>15 min) over walking to the station. Use actual bus fare from TfL if available, else from a pre-extracted BODS fare data file.
2. **Simon**: Add parking costs to his park-and-ride commute, looked up dynamically via National Rail station pages → APCOA fallback.
3. Simon must NOT get bus mode — his non-walk alternative is driving, not bus (already implemented via `park_and_ride=True`).
4. No duplication: parking cost lives in one Data column, referenced by View formula.

## Phase 0 Results (Completed)

**With-bus TfL queries run against all 30 current properties.**

### Bus routes found by TfL (7 properties):

| Route | Location | Operator (from Google) | TfL fare? | Saving vs walk |
|-------|----------|----------------------|-----------|----------------|
| 91 | Knaphill → Woking | Stagecoach South | None | 25m |
| 34 | St Johns → Woking | Stagecoach South | None | 15m |
| 456 | Pyrford → Woking | Stagecoach South | None | negligible |
| 7 | Cox Green → Maidenhead | Reading Buses | £1.75 | negligible |
| 7 | Larchfield → Maidenhead | Reading Buses | £1.75 | none |

### Only 2 properties have meaningful bus savings (≥15 min):** Knaphill (91 bus) and St James Close, Woking (34 bus).

### TfL fare behavior confirmed:
- **Maidenhead bus(7)**: TfL `fare.fares[]` includes bus at £1.75 per ride (London bus flat fare). Use it.
- **Woking buses (91, 34, 456)**: TfL `fare.totalCost=None`, `fare.fares[]` empty. Need external fare lookup.
- `routeOptions.operator` is **always empty** in TfL responses — can't extract operator name from TfL.

### Google Routes API:
- Does NOT return fare data for UK transit.
- DOES return operator name (e.g., Stagecoach South for routes 91/34/456).
- Useful for operator identification as a fallback but not for fares.

### BODS (Bus Open Data Service):
- Fare data available as downloadable NeTEx XML (no per-route query API).
- Stagecoach South dataset: 45MB, covers all Stagecoach South lines.
- Contains line→price mappings within complex zone-based fare structure.

### Parking costs researched (APCOA):
| Station | Daily cost | Source |
|---------|-----------|--------|
| High Wycombe | £10.40 | APCOA (Chiltern) |
| Woking | £12.80 | APCOA (SWR) |
| Fleet | £10.90 | APCOA (SWR) |
| Didcot Parkway | £7.20 | APCOA (GWR) |
| Maidenhead | £9.00 | APCOA (GWR) |
| West Byfleet | £6.00 | APCOA (SWR) |
| Brookwood | £10.80 | APCOA (SWR) |
| Ascot | £10.90 | APCOA (SWR) |
| Egham | £8.70 | APCOA (SWR) |
| Bourne End | £4.00 | APCOA (GWR) |
| Denham | £8.20 | APCOA (Chiltern) |
| Weybridge | £8.70 | APCOA (SWR) |
| Cobham & Stoke d'Abernon | £8.10 | APCOA (SWR) |
| Twyford | £8.40 | APCOA (GWR) |
| Pangbourne | £7.00 | APCOA (GWR) |
| Marlow | Free | No car park |

## Design

### 1. No Bus for Simon

`compute_simon_commute` keeps `allow_bus=False`. Simon's non-walk alternative is `park_and_ride=True` (drive). Bus mode is NEVER in his TfL mode param.

### 2. Lorena: Two-Query Approach

`compute_lorena_commute` runs two TfL queries:

1. **No-bus**: current mode (no "bus") — walking + train/Tube baseline
2. **With-bus**: adds "bus" to mode list

`_pick_best_lorena_route(no_bus, with_bus)`:
- `with_bus` no result → return `no_bus`
- `no_bus` no result → return `with_bus`
- Extract first-leg walk duration from `no_bus`
- Extract first-leg bus/walk duration from `with_bus`
- If `with_bus_leg_duration + 15 < no_bus_leg_duration` → use `with_bus`
- Otherwise → use `no_bus`

(The 15 min accounts for bus waiting + unreliability.)

### 3. `compute_transit` Changes

Add `allow_bus: bool = False`:
- `True`: adds `"bus"` to the mode param in TfL URL
- `False`: current behavior (no bus)

Returns intermediate data (raw JSON) so `compute_lorena_commute` can inspect `fare.fares[]`.

### 4. Bus Fare — Lookup Order

When the with-bus journey is chosen and has bus legs:

**Step 1: Try TfL fare from exact same journey**

If `journey.fare.totalCost` is present and > 0, TfL has priced the entire journey including the bus (as with Maidenhead bus 7). Use `fare.totalCost` directly — no separate bus fare lookup needed:

```python
tfl_total_pence = journey.get("fare", {}).get("totalCost")
if tfl_total_pence and tfl_total_pence > 0:
    daily_cost_gbp = round(tfl_total_pence / 100 * 2, 2)
    # No separate bus cost — TfL already includes it
```

No double-counting: the bus fare is already part of TfL's total. TfL's fare also includes London's daily PAYG cap (zonal caps, bus hopper fare), so `fare.totalCost` is the correct daily cost for the TfL portion.

**Step 2: If TfL gave no fare (totalCost is None/0), look up via stop→zone→price**

This handles the Woking-area buses (91, 34, 456) where TfL can't price the bus leg. The fare depends on which specific stops are used (distance-dependent), so the lookup is by zone pair, not route number.

```python
# data/bus_fares.json loaded at startup — contains the fare MODEL including
# all product types (single, return, day rider):
# {
#   "Stagecoach_South": {
#     "stop_zones": {
#       "knaphill, randolph close": "Zone_Knaphill",
#       "woking, woking railway station": "Zone_Woking_Station"
#     },
#     "zone_fares": {
#       "Zone_Knaphill:Zone_Woking_Station": {
#         "adult_single": 2.50,
#         "adult_return": 4.00,
#         "adult_day": 4.50
#       }
#     }
#   }
# }
# Fare is looked up by (departure_stop, arrival_stop) → zones → zone_pair_price,
# NOT by route number. Uses the cheapest product covering a return trip.
```

Use Google Routes API to identify the operator (since TfL doesn't provide the operator name), then:

1. Extract bus leg's departure stop name and arrival stop name from the TfL response
2. Normalize and look up each in `stop_zones` → `origin_zone`, `dest_zone`
3. Look up `zone_fares["{origin_zone}:{dest_zone}"]` → products dict with `adult_single`, `adult_return`, `adult_day`, etc.
4. Compute daily bus cost from the cheapest product covering two journeys:
   - If `adult_return` exists: use it
   - Else if `adult_day` exists and `adult_day < adult_single * 2`: use `adult_day`
   - Else: use `adult_single * 2`
   - Apply operator `max_single` regulatory cap: `adult_single = min(adult_single, max_single)` before doubling
   - If BODS data includes explicit `daily_cap`: use `min(daily_cap, above)`
5. Apply regulatory caps: `adult_single = min(adult_single, MAX_SINGLE_CAP)` for each operator where applicable (e.g., £3 for Stagecoach South under government scheme)
6. If zone pair not found: log warning with route number and stop names, skip bus cost
7. If operator isn't in data file: log warning, skip bus cost

**Fare caps — two types, both need handling:**

*Type 1: Regulatory/mandatory caps on single fares*
The UK government's National Bus Fare Cap Scheme caps single bus fares at £3 maximum for participating operators (covering Stagecoach South, most others in England). This applies on top of whatever the BODS fare data says — it's a regulatory overlay, NOT encoded in the NeTEx data. The extraction script should check each operator's participation and set a `max_single` value per operator. Applied at runtime: `single = min(bods_fare, max_single)`.

*Type 2: Operator-specific automatic daily capping (tap-on-tap-off)*
Some operators (Reading Buses, TfL) have contactless systems that automatically cap daily spend:
- **TfL**: `fare.totalCost` already includes daily zonal caps — handled by using it directly.
- **Reading Buses**: Tap-on-tap-off with zonal caps (£5.40–£9.00/day). Our Maidenhead bus(7) is priced by TfL, not Reading Buses. If Reading Buses routes appear outside TfL's pricing, the BODS data may contain `CappingRule` elements.
- **Stagecoach South (routes 91, 34, 456)**: No tap-on-tap-off auto capping. Contactless is payment only. The effective daily cap is `min(2×single, dayrider_price, return_price)` subject to the £3 regulatory single cap.

**Daily cost composition** (Lorena with bus when TfL didn't price it):
```
tfl_portion = sum(f["cost"] for f in fares if f.get("mode") != "bus")  # pence from exact journey, exclude bus
bus_daily = _lookup_bus_roundtrip_cost(operator, dep_stop_name, arr_stop_name)
daily_cost_gbp = tfl_portion / 100 * 2 + bus_daily
```

The fare reflects the actual distance: a passenger boarding at Knaphill (far from Woking) pays more than one boarding at Westfield (close to Woking) on the same route 91.

### 5. Parking Cost — Dynamic Lookup

When `park_and_ride=True` and a driving leg replaces walking:

1. Extract station name from `arrivalPoint.commonName`
2. Clean the station name by stripping only station suffixes (" Rail Station", " Underground Station", " Station"). Do NOT strip "London " prefix — the prefix is needed for correct matching.
3. Look up the station's CRS code from `data/stations.csv` by exact-match on cleaned station name (or case-insensitive). Do NOT fuzzy match. If no match found: log error with the station name, set parking cost to None (blank in sheet — indicates parking is needed but we don't know the cost).
4. If station found and has known-free parking (e.g., Marlow — no car park): set parking cost to 0.0 (we KNOW no parking cost).
5. If station found with a car park: try National Rail station page: `GET https://www.nationalrail.co.uk/stations/{crs}/`
   - Parse HTML for daily car park charge
6. Fall back to APCOA: search for station on APCOA website
7. If both fail: log warning, set parking cost to None (blank — parking is needed but unknown cost)

**Semantics of `parking_cost_gbp`:**
- `0.0` = We KNOW parking is free/no charge
- `None` = Parking is required but we couldn't find the cost
- absent = No park-and-ride happened (no driving leg)

Store result in `TransitInfo.parking_cost_gbp`.

**Daily cost composition** (Simon):
```
daily_cost_gbp = (tfl_fare_pence / 100) * 2 + (parking_cost if parking_cost else 0)
```

### 6. Model Changes

```python
class TransitInfo(BaseModel):
    destination_label: str
    destination_postcode: str
    duration_minutes: int | None = None
    daily_cost_gbp: float | None = None
    mode: str = "transit"
    route_summary: str = ""
    parking_cost_gbp: float | None = None   # daily parking if park-and-ride
    bus_cost_gbp: float | None = None       # daily bus fare component if bus used
```

### 7. BODS One-Time Extraction Script

**Script:** `scripts/extract_bus_fares.py`

**Purpose:** Downloads BODS NeTEx fare data for operators in the London commuter belt. Extracts the **fare model** (zone structure + stop-to-zone mappings + zone-pair prices) for routes that serve train stations. Writes the pricing structure to `data/bus_fares.json` so any origin on a known route gets the correct distance-dependent fare at runtime.

**Scope — only routes that serve train stations:**
The extraction filters to routes that have a bus stop within ~200m of a train station (using `data/stations.csv` for station coordinates). This keeps the data focused: we only need fares for the last leg of a bus-to-station journey.

Operators to process (current data + major commuter-belt operators):
- Stagecoach South (SCSO) — needed for current routes 91, 34, 456
- Stagecoach South East (SCSE)
- Stagecoach Oxfordshire (SCOX)
- Stagecoach East Midlands (SCEM)
- Arriva (all relevant divisions)
- First Group (Berkshire, Essex, etc.)
- Reading Buses (READ)
- Metrobus (METR)
- Abellio (ABSS)
- Go-Ahead London (GALD)
- Carousel Buses (CARA)

**How it works — capturing the fare model (not static fares):**

The NeTEx fare data uses a zone-based model: stops are assigned to fare zones, and prices are defined per zone pair (distance matrix). A single route may traverse multiple zones, so the fare depends on how far you travel. The extraction script preserves this structure rather than flattening it.

1. Download BODS NeTEx XML for each operator
2. Parse NeTEx elements:
   - **ScheduledStopPoints**: bus stops with ATCO codes, names, and coordinates
   - **FareZones**: zone definitions and which stops belong to each zone
   - **DistanceMatrixElements**: zone pair → PriceGroup reference (fares between zones)
   - **PriceGroups / Amount elements**: price group → actual GBP amount
   - **Lines**: route numbers and which stops/zones they serve
   - **PreassignedFareProduct**: adult single ticket definition and price
3. Filter to only routes serving train stations:
   - Match bus stop coordinates (from ScheduledStopPoints) against station coordinates (from stations.csv)
   - Keep only routes where at least one stop is within ~200m of a station
4. Build a `data/bus_fares.json` that captures the pricing model, including all fare product types:

```json
{
  "Stagecoach_South": {
    "stop_zones": {
      "knaphill, randolph close": "Zone_Knaphill",
      "woking, woking railway station": "Zone_Woking_Station"
    },
    "zone_fares": {
      "Zone_Knaphill:Zone_Woking_Station": {
        "adult_single": 2.50,
        "adult_return": 4.00,
        "adult_day": 4.50
      },
      "Zone_Knaphill_West:Zone_Woking_Station": {
        "adult_single": 3.00,
        "adult_day": 5.00
      }
    }
  }
}
```

The extraction captures ALL fare products (single, return, day rider, etc.) and any explicit capping rules. These come from:
- **PreassignedFareProduct** + **SalesOfferPackage** elements: ticket types (single, return, day) with prices
- **CappingRule** elements (if present): automatic daily caps applied on contactless PAYG (like Reading Buses' tap-on-tap-off)
- The extraction stores the cheapest `adult_single`, `adult_return`, `adult_day`, and `daily_cap` (if explicit) per zone pair

**Important caveat — BODS data alone is incomplete:**
The BODS NeTEx fare data only contains operator-published fares. It does NOT contain:
- **Government-mandated fare caps** (e.g., the UK-wide £3 max single fare scheme, renewed annually). These are national regulatory overlays applied to all participating operators' fares, not encoded in NeTEx.
- **Operator promotional fares** or special offers not published through BODS.
- **Tap-on-tap-off daily caps** that are computed dynamically (like Reading Buses' zonal caps) — these may or may not have corresponding CappingRule elements in NeTEx.

To handle this, the extraction script includes a **national regulatory overlay** as metadata:

```json
{
  "_meta": {
    "national_max_single_gbp": 3.00,
    "national_max_single_notes": "UK Gov Bus Fare Cap Scheme — applies to all participating operators in England"
  },
  ...
}
```

At runtime, the fare lookup applies: `single = min(bods_published_fare, national_max_single_gbp)` when the cap is set.

This is NOT a list of static route→fare entries. It's the fare **structure**: stops map to zones, zones have pair prices. A new origin stop on a known route automatically gets the correct fare if it falls into an existing zone.

**At runtime**, when a bus leg is found (and TfL didn't price it):
1. Extract the departure stop name from the bus leg (e.g., "Knaphill, Randolph Close")
2. Extract the arrival stop name from the bus leg (e.g., "Woking, Woking Railway Station")
3. Normalize and look up each in `stop_zones` → `origin_zone`, `dest_zone`
4. Look up the zone pair: `zone_fares["{origin_zone}:{dest_zone}"]` → the adult single fare for traveling specifically between those two zones
5. If zone pair not found: log warning, skip bus cost

The lookup is by **zone pair**, not by route. The stop→zone mapping comes from the BODS data, so any stop name we get from TfL maps to a zone, and that zone pair has a specific price. Two different origin stops on the same bus route will produce different fares if they fall in different zones — the fare reflects the actual distance traveled.

**No code change needed** when adding new properties. If the new stop falls into a known zone (or is within the same zone as existing stops), the fare is computed automatically. If the new stop is in a new zone or on a new route/operator that wasn't extracted: re-run the extraction script to update `data/bus_fares.json`.

### 8. Column Changes

**Properties Data tab** — add one column:

| Header | Source |
|---|---|
| Simon Parking Cost (£) | Enriched — from parking lookup |

Single source of truth. Existing `Simon London Cost (£)` includes parking in `daily_cost_gbp`. Parking column is informational breakdown.

**View tab**: No formula changes needed — already XLOOKUPs the cost column.

### 9. Config Changes

```python
# In config.py
bus_walk_penalty_minutes: int = 15  # bus must save this much vs walking to be preferred
```

### 10. Station Name Matching — Exact Match, No Prefix Stripping

When matching station names for parking cost lookup:
- Strip only station suffixes (" Rail Station", " Underground Station", " Station") from the TfL arrivalPoint name
- Do NOT strip "London " prefix — it's needed for disambiguation (e.g., "London Paddington" vs "Paddington" is the same station, but "London Waterloo" is correct; stripping could cause false matches)
- For display purposes (route_summary), continue to use the existing `_shorten_station` function which strips "London " for cleaner output
- Match against `data/stations.csv` `stationName` column by **exact case-insensitive** comparison
- Do NOT fuzzy match — if no match found, log an error with the unmatched station name and set `parking_cost_gbp = None` (blank in sheet)

### 11. Implementation Order

1. Write `scripts/extract_bus_fares.py` — BODS NeTEx extraction, run once
2. Add model fields to `TransitInfo`
3. Add `allow_bus` param to `compute_transit`
4. Implement bus fare lookup (TfL first, data file fallback)
5. Implement `compute_lorena_commute` with two-query comparison
6. Implement parking cost lookup (NR → APCOA)
7. Wire parking into `compute_simon_commute`
8. Add column to sheets.py and server.py
9. Write tests
10. Run extraction script, verify with real data

### 12. Test Plan

| Test | What it verifies |
|---|---|
| `test_lorena_commute_uses_bus_when_much_faster` | No-bus (50m) vs with-bus (30m) → with-bus chosen |
| `test_lorena_commute_rejects_bus_when_not_faster` | No-bus (35m) vs with-bus (33m) → no-bus (only 2m saved) |
| `test_lorena_commute_bus_tfl_fare_used` | TfL `fare.totalCost` > 0 with bus leg → use totalCost, no double-count |
| `test_lorena_commute_bus_zone_fare` | TfL no fare, data file has stop→zone→pair → correct zone-based fare returned |
| `test_lorena_commute_bus_return_cheaper_than_2x_single` | Has `adult_return` (£4.00) vs 2×single (£5.00) → uses return fare |
| `test_lorena_commute_bus_day_rider_cap` | Has `adult_day` (£4.50) vs 2×single (£5.00) → uses day rider |
| `test_lorena_commute_bus_no_cap` | Only `adult_single` available → uses 2×single |
| `test_national_fare_cap_applied` | BODS single is £4.00, national cap is £3.00 → effective single is £3.00, daily cost is £6.00 |
| `test_national_fare_cap_below_cap` | BODS single is £2.50, national cap is £3.00 → effective single stays £2.50 (cap doesn't raise prices) |
| `test_national_fare_cap_not_set` | national_max_single_gbp is null → BODS single used as-is |
| `test_lorena_commute_bus_no_fare_available` | Neither source has fare → bus cost skipped, warning |
| `test_stop_to_zone_mapping` | "Knaphill, Randolph Close" → Zone_Knaphill |
| `test_zone_pair_lookup_with_products` | "Zone_A:Zone_B" → {adult_single: 2.50, adult_return: 4.00} |
| `test_lorena_commute_no_bus_available` | With-bus returns None → falls back to no-bus |
| `test_parking_national_rail_success` | NR station page parsed → correct daily rate |
| `test_parking_apcoa_fallback` | NR fails → APCOA used |
| `test_parking_all_fail` | Both fail → parking_cost_gbp is None (blank), warning logged |
| `test_parking_known_free` | Marlow → parking_cost_gbp is 0.0 |
| `test_parking_station_not_found` | Unknown station → error logged, parking_cost_gbp is None |
| `test_simon_commute_includes_parking` | Park-and-ride with driving leg → parking in daily cost |
| `test_simon_commute_no_driving` | No driving leg → parking_cost_gbp is absent (None) |
| `test_simon_no_bus_mode` | Simon's TfL query never includes "bus" in mode param |
| `test_station_name_matching` | "Woking Rail Station" → matches "Woking", NOT stripped of prefix |
| `test_tfl_fare_includes_bus_no_double_count` | TfL fare.totalCost > 0 with bus leg → daily_cost = fare.totalCost * 2 / 100, no extra bus cost added |
| `test_tfl_fare_no_bus_use_data_file` | TfL totalCost = None with bus leg → data file fare × 2 added to cost |
| `test_bus_fares_file_loaded` | data/bus_fares.json loaded into dict at runtime |

### 13. Files Changed

| File | Change |
|---|---|
| `houses/models.py` | Add `parking_cost_gbp`, `bus_cost_gbp` to `TransitInfo` |
| `houses/enricher.py` | `allow_bus` param, two-query lorena, bus fare (TfL+data), parking cost (NR+APCOA) |
| `houses/config.py` | Add `bus_walk_penalty_minutes` |
| `houses/sheets.py` | Add "Simon Parking Cost (£)" column header, update `_row_values`, column count |
| `houses/server.py` | Add column to `_ENRICHMENT_FIELD_COLUMNS["simon"]` |
| `scripts/extract_bus_fares.py` | NEW: one-time BODS NeTEx extraction → `data/bus_fares.json` |
| `data/bus_fares.json` | NEW: generated by extraction script, loaded at runtime |
| `tests/unit/test_enricher.py` | New test classes for bus selection, bus fare, parking |
| `tests/unit/test_sheets.py` | Update column counts/test data |
| `tests/unit/test_server.py` | Update `DATA_HEADERS` |

### 14. Edge Cases

- **Bus leg but no TfL fare breakdown and no data file entry**: log warning with route number+operator, skip bus cost
- **Multiple bus legs**: sum all unique bus route fares, daily = sum × 2
- **Station name → CRS lookup fails**: log error with unmatched name, set `parking_cost_gbp = None` (blank in sheet). No fuzzy matching.
- **NR page structure changes**: catch parse errors, fall through to APCOA
- **Both parking sources fail**: set `parking_cost_gbp = None` (blank — parking needed but unknown)
- **Known-free parking** (e.g., Marlow — no car park): set `parking_cost_gbp = 0.0` (we KNOW it's free)
- **Simon parking when no driving leg**: `parking_cost_gbp` stays None (not present, not 0, not blank — just absent)
- **TfL fare already includes bus**: Do NOT add an additional bus cost. Use `fare.totalCost` directly.
