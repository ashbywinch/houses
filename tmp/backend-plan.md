# Backend Architecture Plan — Addressing All 20 Gaps

## Summary of Changes

Four architectural changes, then a cleanup pass. Order matters — later steps depend on earlier ones.

---

## Phase 1: Expression System (`dag/expression.py`)

**What:** Replace imperative `compute()` methods with declarative expression trees.

**How:**

```python
# Each node declares its calculation as a tree of Expression objects.
# The base class handles evaluation AND provenance generation.

class DerivedNode(Node[T]):
    @property
    def expression(self) -> Expression[T]:
        raise NotImplementedError

    # Default compute — nobody overrides this
    async def compute(self, *dep_attempts: Attempt) -> Attempt[T]:
        return self.expression.evaluate(dep_attempts)

    # Default build_provenance — nobody overrides this
    async def build_provenance(self) -> Provenance:
        deps = {dep._id: await dep.build_provenance() for dep in self._get_active_deps()}
        formula = self.expression.to_formula() if self.expression else None
        return Provenance(sources=deps, formula=formula)
```

**Expression types needed:**

| Expression | Purpose | Formula output |
|---|---|---|
| `Ref(name)` | Reference a named dependency | Shows dep label + value |
| `Literal(value)` | A constant | Shows the literal |
| `Add(left, right)` | left + right | Two lines with + |
| `Sub(left, right)` | left - right | Two lines with − |
| `Negate(inner)` | -inner | Single line negative |
| `Mul(left, right)` | left × right | Two lines with × |
| `Div(left, right)` | left ÷ right | Two lines with ÷ |
| `PMT(principal, rate, term)` | Monthly payment formula | 4 lines: principal, rate(÷12), periods, result |
| `Sum(*terms)` | Add all terms | One line per term with + |
| `Conditional(pred_fn, if_true, if_false)` | If predicate is true → if_true, else → if_false | Shows both branches, marks which was taken |
| `Choose(*alternatives, selector)` | Evaluate all, pick best, show all with scores | Every alternative shown with ✓/✗ and reason |
| `PerPerson(people_ref, per_person_fn)` | One sub-expression per person | Each person shown separately |

**Key behaviour:** `evaluate()` walks the tree and returns an `Attempt`. If any sub-expression fails, it returns `impossible` BUT the full tree structure is available for provenance. Provenance generation walks the same tree and produces formula lines for every sub-expression regardless of success — showing which deps succeeded, which failed, and which are missing.

**What changes per node:** Each node replaces its `compute()` method with an `expression` property. That's the only change.

**Gaps addressed:** C1 (formulas show real terms), C2 (PMT algorithm visible), C4 (per-person breakdowns via PerPerson), C7 (error chain structural), C8 (partial results in errors), C18 (depth indicator via tree structure), C20 (dynamic deps visible via Conditional/Choose).

---

## Phase 2: Settings as Individual Nodes

**What:** Replace the single `financial_source` UserInputNode[dict] with one UserInputNode per setting.

**New nodes:**

```
mortgage_rate_node             → Money or Decimal   → "Mortgage Rate"        → 4.95%
mortgage_term_node             → int                → "Mortgage Term"        → 27 years
sinking_fund_rate_node         → Decimal            → "Sinking Fund Rate"    → 1.0%
rental_income_node             → Money              → "Rental Income"        → £0/mo
life_insurance_monthly_node    → Money              → "Life Insurance"       → £150/mo
working_weeks_per_year_node    → int                → "Working Weeks/Year"   → 46
current_home_sale_price_node   → Money              → "Current Home Sale"    → £0
current_home_mortgage_node     → Money              → "Outstanding Mortgage" → £0
```

**`AggregateSettingsNode`:** Takes all of the above as deps. Its `expression` is `Sum()` of all (or just lists them). Its `to_json()` returns the same dict shape the frontend expects (backward compat for the settings API endpoint). Its provenance shows each setting as a child with its individual value, freshness, and label.

**Wiring:** Consumer nodes (`MonthlyMortgagePaymentNode`, `YearlySinkingFundNode`, etc.) depend on `mortgage_rate_node` directly, not on the aggregate. The aggregate is for the settings edit page (which doesn't exist yet) and for the frontend's current `/api/settings` endpoint.

**Similarly for `persons`:** If persons becomes individual nodes per person, or at minimum the `PersonUserInputNode` gets a proper `build_provenance()` (no override, default walks deps).

**Gaps addressed:** C5 (config blobs gone — each setting is its own node), C11 (persons label fixed or replaced), C10 (rental_income label becomes its own node), C17 (no more duplication — each setting exists once, referenced by multiple consumers).

---

## Phase 3: Delete All build_provenance() Overrides

**What:** Remove every `async def build_provenance(self) -> Provenance:` override. The base class default handles everything.

**Nodes to clean up:**

| Node | Current override | What happens when deleted |
|---|---|---|
| `CommuteBreakdownNode` | Returns static Provenance, no children | Default walks active commute deps → shows all commute selectors |
| `CouncilTaxNode` | Returns static Provenance, no children | Default walks postcode + best_address → shows lookup inputs |
| `EpcNode` | Returns static Provenance, no children | Default walks postcode + best_address → shows lookup inputs |
| `WalkabilityNode` | Returns static Provenance | Default walks best_location + best_address |
| `NearestTownNode` | Returns static Provenance | Default walks best_location |
| `TownDescNode` | Returns static Provenance | Default walks best_location + nearest_town + town_name + postcode |
| `TownNode` | Returns static Provenance | Default walks best_address |
| `GeocodeNode` | Returns static Provenance | Default walks best_address |

Each of these currently overrides `build_provenance()` to return a label and source_type only. The default already produces that PLUS walks deps. Deleting the override strictly adds information.

**What about nodes that ADD information?** `StampDutyNode` adds a formula. `TotalMonthlyHousingCostNode` adds a formula. These don't override `build_provenance()` — they override `provenance_formula` property, which the default `build_provenance()` reads. With Phase 1, `provenance_formula` becomes obsolete — the expression tree generates formula lines directly.

**Gaps addressed:** C3 (all 8 nodes now show their children), C19 (BestAddressNode shows its priority chain via dep walk).

---

## Phase 4: Commute Decision Transparency

**What:** Make the transit/commute selectors show ALL alternatives with selection rationale.

**Current flow:**
```
TflTransitNode(with_bus=True)  ─┐
                                ├─ TransitNode (picks best) → _get_active_deps returns only selected
TflTransitNode(with_bus=False) ─┘
```

**New flow with `Choose`:**
```python
class TransitNode(DerivedNode):
    expression = Choose(
        Ref("no_bus"),      # TfL with no bus option
        Ref("with_bus"),    # TfL with bus option
        selector=lambda results: min(results, key=lambda r: r.value.duration),
    )
```

`Choose` evaluates all alternatives. `to_formula()` produces:
```
✓ 32 min (selected) — TfL with bus option
✗ 38 min (rejected) — TfL no bus (slower)
```

This also applies to the commute selector which picks between walk/drive/transit for each person, and to rail_fare which conditionally activates based on transit cost.

**Gaps addressed:** C16 (commute internals visible), C20 (rail_fare decision visible).

---

## Phase 5: Cleanup Pass

Small fixes that don't need the new architecture:

| Gap | Fix |
|---|---|
| C9 (status empty label) | Set `SOURCE_LABELS["status"]` or fix the `push()` call |
| C10 (rental_income "default") | Already fixed by Phase 2 (separate node) |
| C11 (persons "db") | Already fixed by Phase 2 |
| C12 (absent data invisible) | Expression tree shows pending/impossible per node |
| C13 (freshness on leaf nodes) | `UserInputNode.build_provenance()` — add `freshness=self._attempt.created_at` |
| C14 (units ambiguous) | Expression types accept a `unit` param, rendered in formula lines: "Life Insurance (monthly)" |
| C15 (best_address freshness) | Add freshness to BestAddressNode — single line change |
| C6 (domain terms unexplained) | Each Expression can carry a `description` text shown alongside the formula line |

---

## Migration Strategy

**Not all at once.** Order:

1. **Build `dag/expression.py`** — core types, evaluate, to_formula
2. **Add `DerivedNode.expression` property** — default returns None (backward compat), nodes opt in
3. **Migrate `StampDutyNode`** — simplest, validates expression + formula path. Delete old `compute()` and `provenance_formula`
4. **Delete 8 `build_provenance()` overrides** — independent change, no expression needed for these
5. **Migrate remaining calculation nodes** — monthly_mortgage, mortgage_required, total_equity, etc.
6. **Settings nodes** — add individual nodes alongside existing blob, then swap consumers
7. **Commute `Choose`** — last, most complex

Each step is independently testable and reversible.
