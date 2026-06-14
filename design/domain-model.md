# Houses — Domain Model

> Generated from the codebase and web-app-dag plan. Every concept, its data, states, relationships, and lifecycle. This is the reference model — all screens derive from here.

---

## Concepts

### 1. Property
**The house itself.** Originates from a Rightmove URL pasted by a user.

| Field | Type | Source |
|---|---|---|
| url | string | User input (Rightmove link) |
| rid | string | Extracted from URL |
| address | string | Rightmove scrape / user |
| postcode | string | Rightmove scrape / user |
| bedrooms | int | Rightmove scrape / user |
| price | float | Rightmove scrape / user |
| actual_latitude | float | User override |
| actual_longitude | float | User override |
| approx_latitude | float | Geocoding |
| approx_longitude | float | Geocoding |
| approx_station_crs | string | Nearest station lookup |
| approx_station_name | string | Nearest station lookup |

**States:** `new → enriched → complete`, `error`
**Lifecycle:** Created by user pasting a Rightmove URL → enrichment populates all derived data → user reviews and makes decisions.
**Relationships:** Has one `Commute` per person (Simon, Lorena, Bracknell). Has one `School` per type (primary, secondary). Has one `CouncilTaxInfo`. Has one `EPC` rating. Has zero or more `UserInputs`.

---

### 2. Commute (per person)
**One person's journey from the property to their destination.**

| Field | Type | Notes |
|---|---|---|
| destination_label | string | e.g. "Pimlico/Victoria", "Aldgate", "Bracknell" |
| destination_postcode | string | |
| duration_minutes | int | Total door-to-door time |
| daily_cost_gbp | Money | Daily travel cost |
| mode | string | "transit" or "drive" |
| cost_groups | tuple[CostGroup] | Breakdown by operator (TfL, NR, parking) |

**Sub-concept — CostGroup:**
- `legs`: JourneyLeg[] — individual segments (walk→tube→walk→train)
- `operator`: string — "TfL", "National Rail", empty (free)
- `cost`: Money | float | None

**Sub-concept — JourneyLeg:**
- `mode`: LegMode (WALK, TUBE, BUS, TRAIN, DRIVE, PARK, etc.)
- `duration_minutes`: int
- `start/end_station`: string
- `line_name`: string

**States:** `computed` / `error` / `missing`
**Provenance:** Source (TfL API, National Rail fares, Google Routes), fallback path, source_time, status_code
**Lifecycle:** Re-computed when location changes (property coords or user config).

---

### 3. CommuteBreakdown
**Aggregated yearly commute costs across all three commuters.**

| Field | Type |
|---|---|
| simon_daily_gbp | float |
| lorena_daily_gbp | float |
| bracknell_daily_gbp | float |
| yearly_total_gbp | float |
| formula_explanation | string |

**States:** `computed` / `partial` (some commutes missing) / `missing`
**Lifecycle:** Derived from individual commute data during enrichment.

---

### 4. School (Primary / Secondary)
**Nearest suitable school for Simon's child.**

| Field | Type | Notes |
|---|---|---|
| name | string | |
| urn | string | Unique reference number |
| phase | string | "Primary", "Secondary" |
| gender | SchoolGender | |
| ofsted_rating | string | "Outstanding", "Good", "Requires Improvement", "Inadequate" |
| inspection_year | string | |
| distance_km | float | From property to school |
| walk_commute | Commute | Walk time from property to school |
| bus_commute | Commute | Bus time (secondary only) |
| link | string | URL to GIAS page |

**States:** `found` / `not_found` / `error`
**Rating semantics:** Outstanding=good, Good=ok, RI/Inadequate=bad
**Lifecycle:** Looked up during enrichment. Depends on property location + child age/gender config.

---

### 5. EPC (Energy Performance Certificate)

| Field | Type | Rating |
|---|---|---|
| rating | string | A–G |
| potential_rating | string | A–G (what it could be after improvements) |
| floor_area | float | m² |
| age_band | string | e.g. "1900-1929" |
| heating_fuel | string | e.g. "mains gas" |
| co2_emissions | float | tonnes/year |

**States:** `found` / `not_found` / `error`
**Rating semantics:** A/B=good, C/D=ok, E/F/G=bad
**Lifecycle:** Queried from EPC API during enrichment. Depends on postcode.

---

### 6. CouncilTaxInfo

| Field | Type | Notes |
|---|---|---|
| band | string | A–H (England) |
| yearly_cost | float | GBP |
| evidence_url | string | Source URL |

**States:** `found` / `not_found` / `error`
**Lifecycle:** Queried during enrichment from council tax API.

---

### 7. Area

| Field | Type | Notes |
|---|---|---|
| town_description | string | LLM-generated prose about the area |
| walk_to_town_minutes | int | Walk time to nearest town centre |
| walkable_amenities | string | Summary of nearby shops, parks, etc. |

**States:** `populated` / `missing`
**Lifecycle:** Geo-derived from property location during enrichment.

---

### 8. Affordability (Derived)
**Financial calculations — monthly costs, stamp duty, mortgage, contributions.**

| Concept | Fields | Dependencies |
|---|---|---|
| StampDuty | amount (float) | price, status (Current=0) |
| MortgageRequired | amount (float) | price, deposit, ashby_contribution |
| MonthlyMortgagePayment | amount (float) | mortgage_required, rate, term |
| YearlySinkingFund | amount (float) | price × sinking_fund_rate |
| MonthlyHousingCost | total (float) | mortgage, sinking_fund, insurance, commute_cost, council_tax, status, rental_income |
| MonthlyCommuteCost | amount (float) | simon/lorena/bracknell daily costs × trips per year |
| NetAshbyContribution | amount (float) | gross_ashby, stamp_duty_share, works_estimate |

**States:** `derived` / `missing_dependency` / `error`
**Lifecycle:** Computed from source data during enrichment. Re-computed when any source field changes.

---

### 9. UserInputs & Status
**Human decisions about the property.**

| Field | Type | Values |
|---|---|---|
| status | enum | "No", "Maybe" (also "Current" for their current home) |
| status_reason | string | Free text |
| design_needed | enum | "Yes", "No" |
| planning_needed | enum | "Yes", "No", "Yikes" |
| ashby_works_estimate | float | £ estimate for renovation work |
| group_notes | string | Free text — shared notes |
| ashby_comments | string | Free text — Ashby's private notes |

**States:** `empty` / `populated`
**Lifecycle:** User-entered at any time. Not affected by enrichment.

---

### 10. NodeResult (Track B — DAG)
**Provenance record for every computed value.**

| Field | Type |
|---|---|
| value | Any |
| status | "ok" / "missing" / "error" |
| error | string |
| source | string (API name) |
| source_detail | string (raw response) |
| source_time | datetime |
| source_status_code | int |
| dep_ids | list[string] |
| compute_info | list |

**States:** `ok` / `missing` / `error`
**Lifecycle:** Created at enrichment time. Persisted in SQLite. Retrieved months later for debugging.

---

## Concept → User Goals

| Concept | User Goals |
|---|---|
| Property | Browse all properties, view property details, add new property, delete property |
| Commute | See commute times for all three people, understand route, check if times are acceptable, see cost breakdown |
| School | See which schools serve the property, check Ofsted rating, walk/bus time for child |
| EPC | Check energy efficiency, see if improvements needed |
| CouncilTax | Check annual cost, see band |
| Area | Read area description, see walkability to town, check local amenities |
| Affordability | Understand total monthly cost, check stamp duty, see mortgage requirements, compare costs across properties |
| UserInputs | Mark property as No/Maybe/Current, add notes, flag design/planning needs, estimate renovation costs |
| NodeResult (DAG) | Debug a value, trace provenance, understand fallback chains, check if enrichment succeeded |

---

## User Goals → Screens

| User Goal | Screen(s) |
|---|---|
| Browse all properties with quick visual status | Property List (overview cards) |
| View full property detail across all dimensions | Property Detail (summary with expandable groups) |
| Drill into commute detail | Commute Drill-Down (within Property Detail or as overlay) |
| Drill into school detail | School Drill-Down |
| Drill into affordability breakdown | Affordability Drill-Down (expanded section) |
| Check EPC and council tax | Key Info section + EPC Drill-Down |
| Read area description | Area section (within Property Detail) |
| Add notes, set status, mark design/planning | User Inputs section (inline in Property Detail) |
| Add a new property | Add Property form (modal or page) |
| Debug provenance (agent) | Graph endpoint (API-only) |
| Compare multiple properties side-by-side | Compare view (maybe future) |

---

## Screen Inventory

| # | Screen | Purpose | Mobile? |
|---|---|---|---|
| 1 | **Property List** | Glanceable overview of all properties | Yes (primary target) |
| 2 | **Property Detail** | Full detail for one property with 5 expandable groups | Yes |
| 3 | **Add Property** | Paste Rightmove URL, optionally edit address/price | Yes |
| 4 | **User Config** | Set offices, car ownership, deposit shares, child age | Yes |
| 5 | **Group Drill-Down** (overlay/in-page) | Full data + provenance for one data domain (commute, schools, affordability) | Yes |

---

## Shared Components

| Component | Used on | Props |
|---|---|---|
| PropertyCard | List | rid, address, price, bedrooms, epc, status, commute_times, monthly_cost |
| RatingBadge | All | value, rating_fn (good/warn/bad), label |
| CommuteRow | Detail, CommuteDrillDown | person, duration, cost, route_summary |
| SchoolBlock | Detail, SchoolDrillDown | school_name, distance, walk_time, ofsted, inspection_year |
| AffordabilityTable | Detail, DetailDrillDown | line items (label, value, color) |
| UserInputBlock | Detail | status, notes, design/planning dropdowns |
| ExpandableSection | Detail | title, summary_value, rating_badge, expanded_content |
| ProvenanceSource | DrillDown (DAG) | source_name, source_time, status, fallback_path |
