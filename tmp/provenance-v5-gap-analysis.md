# Provenance V5 — Gap Analysis

## Part A: Node Catalog

### Dataset A: Total Monthly Cost (`total_monthly_cost`, impossible — errors)

| Node Key | Label (JSON) | SourceType | What It ACTUALLY Is | Value? | Formula? | Freshness? | Missing for a naive user |
|---|---|---|---|---|---|---|---|
| `89306649/total_monthly_cost` | "Total Monthly Cost" | calc | Sum of: mortgage + (yearly_sinking/12×⅔) + life_insurance + (commute_yearly/12) + (council_tax/12) − rental_income. Excludes sinking+life when status="Current". | No — null (impossible) | No — formula returns None on failure | Yes (root) | **Formula chain**: the addition breakdown isn't shown because formula returns None when impossible. User sees error but can't see what the components WOULD have been. **Domain explanation**: what "Total Monthly Cost" includes. |
| `89306649/monthly_mortgage` | "Monthly Mortgage" | calc | PMT = principal × (monthly_rate × (1+rate)^n) ÷ ((1+rate)^n − 1). principal = mortgage_required. rate = financial.mortgage_rate/12, n = financial.mortgage_term_years×12. | No — null (impossible) | Shows principal, rate, term — but NOT the PMT formula itself | Yes | **Algorithm hidden**: user sees "4.95%, 27 years, £X principal" but not how they combine into a payment. **Actual formula string missing**: "PMT = P × r(1+r)^n / ((1+r)^n−1)" would let a user verify. **Zeros not explained**: if principal=0, node returns £0 with no explanation. |
| `89306649/mortgage_required` | "Mortgage Required" | calc | max(0, rightmove_price + stamp_duty + total_works − total_equity) | No — null | Formula shows only "Mortgage Required = value" — **does not break down the 4 terms** | Yes | **CRITICAL**: The formula line says "Mortgage Required = £X" — it does NOT show Price + Stamp Duty + Works − Equity. User must manually trace each child node. No indication of which sign each term has (+ or −). |
| `89306649/rightmove_price` | "Rightmove" | user | User-entered property price from Rightmove listing | **GBP 800,000.00** | N/A (leaf) | No freshness field | **No link to Rightmove listing URL**. **No age indicator** — user can't tell if this was entered today or last month. **No units shown** — GBP appears in value string but not as a separate field. |
| `89306649/stamp_duty` | "Stamp Duty" | calc | SDLT: 0% on first £250k, 5% on £250k–£925k portion, etc. (computed by `stamp_duty_land_tax()`). Status "Current" → £0. | **GBP 27,500.00** | Shows Property Price + "First-time buyer relief: N/A" | Yes | **Rate bands not shown**: the actual SDLT bracket logic is invisible. User sees price + result but not "0% on first £250k, 5% on £250k–£675k". **Domain term unexplained**: what IS stamp duty? **First-time buyer relief always "N/A"**: even when it could apply, no explanation of why. |
| `89306649/total_works` | "Total Works" | calc | Sum of works_estimates dict. Gates: any non-child person with `works_estimate_required=True` and no estimate blocks the whole calculation. | No — null (error) | Formula shows only "Total Works = value" — no breakdown | Yes | **Per-item breakdown missing**: the formula doesn't list individual work items. **Error message opaque**: "Works estimate required for: Ashby" — user sees this in description but it's non-obvious what action to take. **No link to the person who needs to provide the estimate**. |
| `89306649/total_equity` | "Total Equity" | calc | Per person: max(0, home_sale_price − outstanding_mortgage) + (if not Current) cash_contribution. Summed across all persons. | **GBP 477,000.00** | Formula shows only "Total Equity = value" — **no per-person breakdown** | Yes | **CRITICAL**: Formula doesn't show individual person contributions. User can't see "Simon contributed £177K (sale £550K − mortgage £373K)" or "Ashby contributed £300K cash". **Components invisible**: home_sale_price, outstanding_mortgage, cash_contribution per person. **"Current" exclusion invisible**: if status excluded cash contributions, user can't tell. |
| `persons` (key: "persons") | "db" | user | `UserInputNode[list[Person]]` — persisted list of persons with their financial details (home_sale_price, outstanding_mortgage, cash_contribution, etc.) | No (no value on this node) | N/A (leaf, though technically a UserInputNode) | None in JSON | **Label "db" is meaningless** to a naive user. **No evidence of person data** — user can't see how many persons, their names, or their contributions. **No source attribution** — was this loaded from DB, config defaults, or user-edited? **No freshness**. |
| `89306649/status` | "" (empty) | user | `comment_status` UserInputNode — free-text field like "Current" (owner-occupied) or empty | No (no value shown in JSON) | N/A | None in JSON | **Label is empty string** — completely opaque. **No hint** that "Current" means owner-occupied and changes which costs apply. **No explanation** of possible values. |
| `financial` (key: "financial") | "config" | user | `UserInputNode[dict]` — financial settings: mortgage_rate, term, sinking_fund_rate, etc. | **Full dict shown** (mortgage_rate: 0.0495, etc.) | N/A | None in JSON | **Rate shown as decimal (0.0495)** not as "4.95%". **No explanation per field** — user sees `mortgage_rate`: 0.0495 and must guess. **SourceType says "user"** but label says "config" — contradictory. **No per-setting freshness**. **Includes irrelevant fields** (rental_income_monthly:0, working_weeks_per_year:46, etc.) with no indication which are used. |
| `89306649/yearly_sinking_fund` | "Yearly Sinking Fund" | config | rightmove_price × sinking_fund_rate (from financial config) | **GBP 8,000.00** | No formula (node sets source_type=CONFIG but doesn't override provenance_formula) | Yes | **No formula** showing `£800,000 × 0.01 = £8,000`. **Domain term unexplained**: what is a sinking fund? **Rate not shown**: sinking_fund_rate=0.01 is in the financial blob but no connection is drawn. |
| `89306649/life_insurance_total` | "Life Insurance Total" | calc | Sum of all persons' `life_insurance_monthly` | **GBP 150.00** | Formula shows only "Life Insurance Total = value" — **no per-person breakdown** | Yes | **Per-person breakdown missing**: user can't see who has what insurance (Simon £100, Lorena £50). **Unit confusion**: is this monthly or yearly? (Answer: monthly, but not stated.) |
| `89306649/rental_income` | "default" | user | `UserInputNode[Money]` — user-entered monthly rental income | **GBP 0.00** | N/A | None in JSON | **Label is "default"** — should be "Rental Income". **No explanation** that this is SUBTRACTED from total cost. **No freshness**. |
| `89306649/commute_breakdown` | "commute_breakdown" | calc | CommuteBreakdownNode — aggregates all persons' commute costs into a dict with `yearly_total_gbp` and per-person breakdowns | No (no value in JSON) | No (no formula override) | Yes | **No child sources in provenance** — `build_provenance()` returns a static Provenance that doesn't walk dependency nodes. User can't drill into commute details from here. **No value visible** — the computed yearly_total_gbp is missing from the Provenance. |
| `89306649/council_tax` | "Council Tax" | api | CouncilTaxNode — looks up band and yearly cost from gov.uk | No (no value) | N/A | Yes | **No child sources in provenance** — `build_provenance()` returns a static Provenance without walking deps. **No cost shown**. URL present ✓. |

---

### Dataset B: Council Tax Error (ambiguous address)

| Node Key | Label (JSON) | SourceType | What It ACTUALLY Is | Value? | Formula? | Freshness? | Missing for a naive user |
|---|---|---|---|---|---|---|---|
| `89306649/council_tax` | "Council Tax" | api | Failed Council Tax lookup — address "31 Isambard Road" matched Band D (£1,905/yr) and Band E (£2,329/yr) | No — null (error) | N/A | Yes | **Good**: Description clearly explains ambiguity ✓. Error field present ✓. URL present ✓. **Missing**: A visual distinction between "we found 2 options" vs "we found nothing". **Missing**: A way to select which band to use as a resolution. |
| `postcode` | "postcode" | user | User-entered postcode | **UB2 4GN** | N/A | Yes | Looks clean. Label is low-context ("postcode"). |
| `address` | "best_address" | calc | BestAvailableAddress — the resolved address used for lookup | **31 Isambard Road, Southall, UB2 4GN** | N/A | Yes | Looks clean. |

---

### Dataset C: Commute Error (TfL 409)

| Node Key | Label (JSON) | SourceType | What It ACTUALLY Is | Value? | Formula? | Freshness? | Missing for a naive user |
|---|---|---|---|---|---|---|---|
| `commute_breakdown` (root) | "commute_breakdown" | calc | CommuteBreakdownNode — in the fabricated JSON this shows sources, but **the real `build_provenance()` returns a static Provenance with no sources** | No | No | Yes | **Real code has no child sources** — the fabricated JSON shows `Simon/Office` as a child, but actual code drops all dependency provenance. **No value** shown for the aggregate. |
| `Simon/Office` | "Simon/Office" | api | PetrolCostAugmentNode wrapping MergeRailFareNode wrapping CommuteSelectorNode — the final commute result for Simon's trip to Pimlico. Fabricated JSON says description "Walking + train to Pimlico". | No | N/A | Yes | **Mixed sourceType**: labeled "api" but actually a chain of calc nodes wrapping an API result. **No cost shown**: even if commute succeeded, no daily/weekly/monthly cost visible. **No breakdown** of walk vs train vs petrol costs. |
| `walk` | "walk" | api | WalkNode — Google Maps walking route | **19 min** | N/A | Yes | Reasonably clean. |
| `transit` | "Southall → Ealing Broadway → Oxford Circus → Pimlico" | api | TflTransitNode — TfL public transit routing | No (error) | N/A | Yes | **Good**: error message explains 409 and engineering works ✓. Route name descriptive ✓. URL present ✓. **Missing**: No indication that this is ONE of several options (there's also a `with_bus` variant and `no_bus` variant that the TransitNode selects between). |
| `rail_fare` | "rail_fare" | api | RailFareNode — National Rail fare lookup to replace TfL's £0 transit cost | No | N/A | Yes | **No value shown** — even when lookup succeeds, the fare isn't surfaced in provenance. **No explanation** of when/how NR fare applies. **No station names shown** — origin and destination stations are computed internally. |

---

### Dataset D: EPC Rating (clean)

| Node Key | Label (JSON) | SourceType | What It ACTUALLY IS | Value? | Formula? | Freshness? | Missing for a naive user |
|---|---|---|---|---|---|---|---|
| `89306649/epc` | "EPC Rating" | api | EPC register lookup result | **Band C (68)** | N/A | **8 days old** (2026-07-22) | Reasonably complete. Description explains source ✓. URL to register ✓. Value shown ✓. Freshness visible ✓. **Could improve**: show the address that was looked up, not just postcode. |
| `postcode` | "postcode" | user | User-entered postcode | No (no value on this node) | N/A | Yes | Clean. |
| `best_address` | "best_address" | calc | BestAddressNode — resolved from user_entered_address, corrected_address, or rightmove_address | No | N/A | No freshness | **Own freshness missing** — when was this address resolved? The `best_address` node has no freshness on itself. **Source priority invisible** — which address won and why? |
| `user_entered_address` | "user_entered_address" | user | User-typed address | (no value shown) | N/A | Yes | Clean. |
| `rightmove_address` | "rightmove_address" | user | Address scraped from Rightmove | (no value shown) | N/A | Yes | Clean. |

---

## Part B: Chain Traceability

### Dataset A — Total Monthly Cost Chain (broken chain)

```
total_monthly_cost (impossible — "Works estimate required; no council tax data")
  │
  ├── monthly_mortgage (impossible — "Works estimate required")
  │     │
  │     ├── mortgage_required (impossible — "Works estimate required")
  │     │     ├── rightmove_price ← GBP 800,000 ✓ (user, value shown)
  │     │     ├── stamp_duty ← GBP 27,500 ✓ (calc, value + partial formula)
  │     │     ├── total_works ← ✗ ERROR (persons, works_estimates)
  │     │     └── total_equity ← GBP 477,000 (calc, value but NO per-person formula)
  │     │
  │     └── financial ← (config, dict blob, no per-key breakdown)
  │
  ├── yearly_sinking_fund ← GBP 8,000 (config, NO formula)
  ├── life_insurance_total ← GBP 150 (calc, NO per-person formula)
  ├── rental_income ← GBP 0 (user, label "default" — confusing)
  ├── status ← "" (user, EMPTY LABEL)
  ├── financial (DUPLICATE — same as under monthly_mortgage)
  ├── commute_breakdown ← (calc, NO sources, NO value)
  ├── council_tax ← (api, NO sources, NO value)
  └── persons ← "db" (user, opaque — no person details visible)
```

**Critical traceability gaps:**

1. **mortgage_required → formula**: The formula is `max(0, price + stamp_duty + works − equity)` but the formula output shows only the final value. User must manually trace each child. **Each term's sign is invisible** — does equity add or subtract?

2. **total_equity → per-person**: Equity is `Σ max(0, sale − mortgage) [+ cash]` per person, but neither the formula nor the children break it down. User sees `£477,000` but can't tell how much comes from Simon's home sale vs Ashby's cash contribution.

3. **monthly_mortgage → PMT formula**: The PMT calculation (principal × r(1+r)^n / ((1+r)^n − 1)) is completely hidden. The formula only shows inputs, not the algorithm.

4. **yearly_sinking_fund → no formula**: Shows £8,000 but doesn't explain that this is price × 1% rate. The rate is buried in the `financial` blob.

5. **life_insurance_total → per-person**: Shows £150 but doesn't explain this is Simon (£100) + Lorena (£50).

6. **commute_breakdown → drops all children**: The `build_provenance()` override returns a static Provenance that doesn't include any commute selector nodes. The user **cannot** drill from total_monthly_cost into commute costs.

7. **council_tax → drops all children**: Same issue — static Provenance with no best_address or postcode children.

8. **financial → appears twice** (under total_monthly_cost AND under monthly_mortgage). The frontend detects this as a "shared ref" but it suggests the model has duplication issues.

9. **status → empty label**: The node exists but `""` as a label provides zero context. User can't tell what "status" means and that it controls which costs are included/excluded.

10. **persons → opaque**: The persons source appears only as "db" with no indication of its contents. Five people with complex financial details are invisible.

### Dataset B — Council Tax Error Chain

```
council_tax (impossible — ambiguous address)
  ├── postcode ← "UB2 4GN" ✓
  └── address ← "31 Isambard Road, Southall, UB2 4GN" ✓
```

**Traceability**: Good. Error description explains exactly what happened. Both inputs visible. But note: **the real code doesn't produce these child sources** — the real `CouncilTaxNode.build_provenance()` is static and drops them. This dataset is aspirational.

### Dataset C — Commute Error Chain

```
commute_breakdown
  └── Simon/Office
        ├── walk ← "19 min" ✓
        ├── transit ← ✗ ERROR (TfL 409 — engineering works)
        └── rail_fare ← (api, no value, no error)
```

**Traceability gaps:**

1. **Real `commute_breakdown.build_provenance()` drops all children** — the fabricated JSON shows Simon/Office as a child, but real code doesn't.
2. **`Simon/Office` is a chain of 4+ nodes** (CommuteSelectorNode → MergeRailFareNode → PetrolCostAugmentNode) but appears as one node. User can't see that walk/transit/drive were compared and the shortest was selected.
3. **Multiple transit options collapsed**: The pipeline creates BOTH a `no_bus` and `with_bus` TfL transit node, then TransitNode picks. Only the selected one (or the error from the selected one) surfaces. The other option is invisible.
4. **rail_fare has no error state**: It shows as a node with no value and no error — user can't tell if it succeeded, failed, or wasn't attempted.
5. **No cost shown**: Even for successful walk (19 min), there's no cost breakdown. RailFareNode would have a fare if it succeeded.

### Dataset D — EPC Rating Chain

```
epc ← "Band C (68)" (api, 8 days old)
  ├── postcode ← (user, freshness shown)
  └── best_address
        ├── user_entered_address ← (user, freshness shown)
        └── rightmove_address ← (user, freshness shown)
```

**Traceability**: Reasonably clean. The chain is short and each step is traceable.
**Minor gap**: `best_address` has no freshness of its own (derived from children, but not declared).

---

## Part C: Missing Data Catalog

### Critical Gaps (the model fundamentally can't explain the calculation)

| # | Gap | What the user needs | Fix type |
|---|---|---|---|
| **C1** | **Formulas show only "X = value" not the actual terms** | `mortgage_required`, `total_equity`, `total_works`, `life_insurance_total` all override `provenance_formula` to return a single line showing the result only. The individual term values and operators are invisible. | Backend: `provenance_formula` must include each term with its sign. E.g., MortgageRequired = Price + StampDuty + Works − Equity, each as a FormulaLine. |
| **C2** | **PMT formula algorithm is invisible** | `monthly_mortgage` shows rate, term, and principal but not the formula string `PMT = P × r(1+r)^n / ((1+r)^n−1)`. A naive user can't verify the payment. | Backend: Add formula representation for non-trivial algorithms. Either a textual formula string or step-by-step lines showing numerator/denominator. |
| **C3** | **build_provenance overrides drop all dependency children** | `CommuteBreakdownNode`, `CouncilTaxNode`, `EpcNode`, `WalkabilityNode`, `NearestTownNode`, `TownDescNode`, `TownNode`, `GeocodeNode` all override `build_provenance()` with a static Provenance that doesn't walk dependency nodes. User can't drill in. | Backend: Every `build_provenance()` should call `super().build_provenance()` or manually walk deps. Or remove overrides entirely and let the default walk do it. |
| **C4** | **Per-person breakdowns are missing from equity, life insurance, works** | `total_equity` computes per-person equity but aggregates into one number. `life_insurance_total` sums per-person premiums. `total_works` sums estimates. None expose the individual contributions. | Backend: Formula lines should include one line per person showing their individual contribution. |

### High-Impact Gaps

| # | Gap | What the user needs | Fix type |
|---|---|---|---|
| **C5** | **Config/settings nodes are opaque blobs** | `financial` and `persons` are `UserInputNode[dict]` / `UserInputNode[list[Person]]` — their values are dumped as raw JSON blobs. The user sees `{"mortgage_rate": 0.0495, ...}` without explanations, units, or field-level freshness. | Backend: Break financial settings into individual nodes per field, OR add a settings display schema that maps keys → labels, units, and descriptions. |
| **C6** | **Domain terms have no explanations** | "Stamp Duty", "Sinking Fund", "Equity", "Mortgage Term", "Interest Rate", "PMT" all appear without definitions. The frontend has `HUMAN_LABELS` but no mechanism for explanations. | Frontend: Add a definition/glossary system (tooltip, tap-to-explain, or info icon). Backend: Optionally add `description` field for domain terms. |
| **C7** | **Error chain is text-only** | When a middle node fails (e.g. `total_works` requires an estimate), the error propagates up as a text description "Works estimate required for: Ashby". The upstream nodes show this text but there's no visual error indicator distinct from a normal node. | Frontend: Error status should be visually distinct (color, icon, banner). Backend: Ensure `status: "impossible"` propagates with meaningful per-node errors. |
| **C8** | **No intermediate values in error states** | When `total_monthly_cost` is impossible, its formula returns None. But the user CANNOT see what the partial sum would have been — which parts succeeded and which failed. A naive user can't tell "£800K price + £27.5K stamp duty + £477K equity = ✓, but works estimate = missing". | Backend: `provenance_formula` should return a partial formula even when impossible, showing which deps succeeded with their values and which failed with errors. |

### Medium-Impact Gaps

| # | Gap | What the user needs | Fix type |
|---|---|---|---|
| **C9** | **The `status` node has label "" (empty string)** | `89306649/status` appears with no label. This is the "Current" flag that controls whether sinking fund and life insurance are excluded. | Backend: Set a meaningful source_label on the UserInputNode push, or add a human_label mapping in the frontend. |
| **C10** | **`rental_income` shows label "default"** | Should be "Rental Income" or "Monthly Rental Income". | Backend: Fix source_label on push. |
| **C11** | **`persons` shows label "db" with no indication of contents** | Should indicate that it contains personal financial data for household members (Simon, Lorena, Ashby, George). | Backend: Either expand `build_provenance` for the persons node or add a richer label. |
| **C12** | **Absent data is silently invisible** | When `commute_breakdown` has no value, it appears as a node with no value and no error. When `council_tax` lookup simply hasn't been done yet (pending), it appears as a node with no value. Users can't distinguish "not yet fetched" from "empty result" from "failed". | Backend: Ensure `status: "pending"` is distinguishable. Frontend: Show pending states as gray/neutral rather than absent. |
| **C13** | **Freshness is missing on many leaf nodes** | `rightmove_price`, `rental_income`, `status`, `persons`, `financial` all have no freshness field. User can't tell when these were last changed. | Backend: UserInputNode should persist/surface `created_at` or `updated_at`. |
| **C14** | **Units are ambiguous** | Life insurance: "GBP 150.00" — is this monthly or yearly? (monthly). Sinking fund: "GBP 8,000.00" — yearly. Commute costs — yearly. Council tax — yearly but shown as monthly in total. None of these units are annotated. | Backend: Add unit metadata (monthly/yearly/total) to Provenance or formula lines. |
| **C15** | **The `best_address` node has no freshness of its own** | It's derived from children but doesn't expose when it was last resolved. | Backend: Add freshness to BestAddressNode's build_provenance. |

### System-Level / Architectural Gaps

| # | Gap | What the user needs | Fix type |
|---|---|---|---|
| **C16** | **Commute pipeline has 10+ internal nodes but provenance shows 0** | For each person/POI, the pipeline builds: WalkNode, DriveNode, TflTransitNode (×2), TransitNode, ParkAndRideAugmentNode, BusRouteNode, BodsFareNode, BusLegAugmentNode, CommuteSelectorNode, MergeRailFareNode, PetrolCostAugmentNode — most of which don't surface in provenance. The user sees only `commute_breakdown` with no internals. | Backend: Either (a) the terminal commute nodes should bubble up provenance, or (b) the final PetrolCostAugmentNode should build a comprehensive provenance tree that includes the selected route details. |
| **C17** | **Duplicate config references waste space and confuse** | `financial` appears as a source under both `total_monthly_cost` and `monthly_mortgage`. `persons` appears under `total_works`, `total_equity`, `life_insurance_total`. The frontend "shared refs" feature mitigates this but doesn't solve the underlying structural duplication. | Architecture: Either deduplicate in the backend (use one `PersistentNode` per config singleton) or in the frontend (collapse identical refs by key). |
| **C18** | **No calculation depth indicator** | A naive user can't tell whether a node is a leaf (input), an intermediate calculation, or the final result — without reading the tree depth. | Frontend: Visually distinguish input/calculation/result nodes (e.g., border style, background tint, icon). The story view in ProvenanceView.vue attempts this (sections 1/2/3) but only works for flat top-level children. |
| **C19** | **BestAddressNode priority chain invisible** | The node chooses from user_entered > corrected > rightmove, but the provenance doesn't show which source won or why. | Backend: Add a formula-like "priority chain" to BestAddressNode's provenance showing which source was selected and the fallback order. |
| **C20** | **RailFareNode's active deps are dynamic (load-bearing for correctness) but provenance doesn't reflect this** | When transit has a cost, `RailFareNode._get_active_deps()` excludes best_location. When transit is free, it includes best_location to compute NR fare. The user can't tell whether rail fare was attempted or skipped. | Backend: Provenance should indicate which deps were active and why. |

### What the Fabricated JSON Datasets Show That Real Code Misses

The 4 datasets in the brief are aspirational — they show features that don't exist in the actual code:

| Aspirational Feature | In Dataset | Real Code Status |
|---|---|---|
| `council_tax` has child sources (postcode, address) showing the inputs that failed | B | Real `CouncilTaxNode.build_provenance()` returns static Provenance with no children |
| `commute_breakdown` has child sources (Simon/Office) you can drill into | C | Real `CommuteBreakdownNode.build_provenance()` returns static Provenance with no children |
| `Simon/Office` has children (walk, transit, rail_fare) showing commute sub-components | C | Real final commute node doesn't surface these children |
| `stamp_duty.formula.lines` show meaningful breakdown (Property Price + First-time Buyer Relief) | A | Real code does produce this ✓ |
| `status` field with `"impossible"` and `"error"` messages | B, C | Real code does produce these for failures ✓ |
| Per-node `freshness` on user inputs | A, B, D | Real `UserInputNode.build_provenance()` doesn't include freshness |
| `url` field on API nodes | B, C, D | Real EPC/CouncilTax nodes do provide URL ✓ |
| `postcode` with freshness as user input | B, D | Real postcode UserInputNode does surface this |
