# Plan: Migrate Cost of Works into DAG

## Goal

Migrate the Ashby Works Estimate financial logic from spreadsheet formulas into
Python DAG nodes, display it in the frontend Costs section with inline editing,
and gate the affordability chain so missing estimates produce "Impossible".

---

## Current State

**Spreadsheet** (formulas in `DATA_FORMULA_COLS`, View tab manual column):

```
AshbyWorksEstimate = [View tab, manual entry per property]
NetAshbyContribution = GrossAshbyContribution − StampDuty/3 − AshbyWorksEstimate
    capped at min(na, price/3), 0 if Status=Current
MortgageRequired = Price − Deposit − NetAshbyContribution              ← BUG
MonthlyMortgage = PMT(MortgageRate/12, MortgageTermYears*12, −MortgageRequired)
```

**DAG** (`MonthlyMortgagePaymentNode.compute()`):

```python
total_equity = sum(persons.deposit_equity)  # Simon has £177k
principal = price + stamp_duty − total_equity                            ← BUG
monthly = PMT(rate, term, principal)
```

Both models have bugs (see below).

---

## The three buyers

Simon, Lorena, Ashby. Person objects get **global** financial fields
(same across all properties):

```python
@dataclass(frozen=True)
class Person:
    name: str
    ...
    home_sale_price: Money = Money("0", "GBP")           # proceeds if selling
    outstanding_mortgage: Money = Money("0", "GBP")       # remaining mortgage
    cash_contribution: Money = Money("0", "GBP")          # cash they bring
    works_estimate_required: bool = False                   # if True, missing → impossible
```

### Works is per property AND per person

Each property stores a dict of `{person_name: works_amount}` in a single
per-property DAG node. Names are **data keys**, never in DAG code.

```
# Per-property node `works_estimates` stores:
{"Simon": 0, "Lorena": 0, "Ashby": 20000}

# Total works for this property = sum of all values
```

The sheet column "Ashby Works Estimate" populates Ashby's entry in the dict.
Simon's and Lorena's entries default to 0 until they get their own columns.

Removed from Person: `deposit_equity` (replaced by home_sale_price −
outstanding_mortgage).

---

## Corrected formula

**Actual values from the sheet Constants tab:**
```
Current Sale Price       = £550,000
Outstanding Mortgage     = £373,000
Gross Ashby Contribution = £300,000
```

All properties currently have empty "Ashby Works Estimate" — hence broken
monthly figures. The migration gates on `works_estimate_required`.

### Money flows — trace with Price=£500k, SD=£15k, Works=£20k

**Ashby's cash pays his SD share and works first:**
```
Cash contribution     £300k
  − SD/3              −£5k   (his equal share of stamp duty)
  − Works             −£20k  (renovation costs)
  = Remainder to house £275k
```

**Simon & Lorena:**
```
Deposit from sale     £177k  (550k − 373k) → to house price
Their SD share (2/3)  £10k  → from their mortgage
```

**Mortgage formula — two equivalent views:**
```
Per-party:  Price − Deposit − (CashContrib − SD/3 − Works) + 2/3×SD
          = 500k − 177k − (300k − 5k − 20k) + 10k = £58k

Aggregate:  Price + SD + Works − Deposit − CashContrib
          = 500k + 15k + 20k − 177k − 300k = £58k ✅
```
The aggregate form is simpler: total cost minus total cash. The SD/3 split
is a provenance detail (shown in the `CashContributionBreakdownNode`),
not a formula change.

With `works_estimate_required=True` and no estimate entered:
→ Impossible (gated at `TotalWorksNode`)

### Bugs in current models

| Model | Formula | Result | Problem |
|---|---|---|---|
| Sheet | `Price − Deposit − NetAshby` | £225k | ❌ missing +2/3×SD (£10k) |
| DAG | `Price + SD − PersonsEquity` | £338k | ❌ adds all SD, no cash contribution |
| **Correct** | `Price + SD + Works − Deposit − CashContrib` | **£235k** | ✅ |

---

## DAG Node Chain

All values sourced from `persons_source` (summing across Person objects).
`financial_source` only holds: mortgage_rate, mortgage_term_years, sinking_fund_rate, etc.

```
                               ┌──────────────────────────────┐
   works_estimates_node ───────┤                              │
   persons_source ─────────────┤   TotalWorksNode             ├──→ impossible if any
   (works_estimate_required)   │         (Money)              │    required person missing
                               └──────────────┬───────────────┘
                                              │ total_works
                                              ▼
                               ┌──────────────────────────────┐
   persons_source ─────────────┤                              │
   (home_sale_price,           │   EquityTotalNode            │
    outstanding_mortgage,      │         (Money)              │
    cash_contribution)         │                              │
                               └──────────────┬───────────────┘
                                              │ total_equity
                                              ▼
                               ┌──────────────────────────────┐
   rightmove_price ────────────┤                              │
   stamp_duty ─────────────────┤   MortgageRequired           ├──→ impossible if either
   total_works ────────────────┤         (Money)              │    dep impossible
   total_equity ──────────────┤                              │
                               └──────────────┬───────────────┘
                                              │ mortgage_required
                                              ▼
                               ┌──────────────────────────────┐
   mortgage_required ──────────┤                              │
   financial_source ───────────┤   MonthlyMortgagePayment     ├──→ impossible if required
   (rate, term)                │         (Money)              │    impossible
                               └──────────────┬───────────────┘
                                              │ monthly_payment
                                              ▼
                               ┌──────────────────────────────┐
   monthly_payment ────────────┤                              │
   yearly_sinking ─────────────┤   TotalMonthlyHousingCost    ├──→ impossible if mortgage
   commute_breakdown ──────────┤         (Money)              │    impossible
   council_tax ────────────────┤                              │
   financial_source ──────────┘                              │
                               └──────────────────────────────┘
```

---

## Migration strategy: no rebuild needed

The existing spreadsheet has real data in three places that needs to flow
into the new Person-based model:

| Where | What | Compute |
|---|---|---|
| Constants tab: Current Sale Price (£) − Outstanding Mortgage (£) | Simon's equity contribution | `Simon.home_sale_price`, `Simon.outstanding_mortgage` |
| Constants tab: Gross Ashby Contribution (£) | Ashby's cash contribution | `Ashby.cash_contribution` |
| View tab: Ashby Works Estimate (£) | Ashby's works per property | `works_estimates["Ashby"]` |

`EquityTotalNode` computes each person's total equity from their granular
fields and sums them. The home_sale/mortgage/cash fields stay on Person for
configuration/display — the DAG does the calculation.

### Migration flow

1. **Bootstrap already reads the Constants tab** via `load_property_nodes_from_db()`
   and `load_property_nodes_from_rows()`. We add a step that maps constants
   → persons_source:

```python
def _migrate_constants_to_persons(persons_source, financial_source):
    """Called once at startup — reads Constants values onto Person objects."""
    fin = financial_source.latest_attempt().value_or_none() or {}
    persons = list(persons_source.latest_attempt().value_or_none() or [])
    
    # Simon gets the home sale values
    simon_idx = _find_person(persons, "Simon")
    if simon_idx is not None and fin.get("current_home_sale_price"):
        p = persons[simon_idx]
        persons[simon_idx] = dataclasses.replace(p,
            home_sale_price=Money(str(fin["current_home_sale_price"]), "GBP"),
            outstanding_mortgage=Money(str(fin.get("current_home_outstanding_mortgage", 0)), "GBP"),
        )
    
    # Ashby gets the cash contribution
    ashby_idx = _find_person(persons, "Ashby")
    if ashby_idx is not None and fin.get("gross_ashby_contribution"):
        p = persons[ashby_idx]
        persons[ashby_idx] = dataclasses.replace(p,
            cash_contribution=Money(str(fin["gross_ashby_contribution"]), "GBP"),
        )
    
    persons_source.push(persons, "migration")
```

This is a one-time push at startup — after that, edits flow through the
normal persons_source PATCH flow.

### Constants tab → one-shot migration script

The Constants tab holds the real values (550k sale, 373k mortgage, 300k
contribution). A one-shot script reads the sheet and pushes to the settings
API. After that, edits flow through `PATCH /api/settings/financial` and
`PATCH /api/persons` — no sheet reads on startup.

Add `"gross_ashby_contribution": 0` to `make_default_financials()` so the
key exists at all times (default until user configures it). The migration
script overwrites the defaults with real sheet values.

**Limitation**: if someone edits the Constants tab after migration, those
changes won't auto-sync. They'd re-run the migration script or use the
settings UI.

### View tab — works estimate per property

The "Ashby Works Estimate" column is on the View tab, but bootstrap
only reads the Data tab. Add a View tab reader:

1. `get_properties_view_data()` in `houses/sheets/reader.py` — same pattern
   as `get_properties_data()`, returns `list[dict]` keyed by header name.

2. During `load_property_nodes_from_rows()`, read View tab rows and merge
   "Ashby Works Estimate" by **Rightmove ID** only (column D in View, H in
   Data). If IDs don't match, log a warning and skip that row — never fall
   back to row index.

3. Push the merged value into the per-property `works_estimates` node as
   a dict: `{"Ashby": parsed_value}`. The full bootstrap also sets defaults
   `{"Simon": 0, "Lorena": 0}` so the dict is always complete.

### DB node ID migration — float→dict

Old node: `{rid}/ashby_works` (`UserInputNode[float]`)
New node: `{rid}/works_estimates` (`UserInputNode[dict]`)

The type changes from `float` to `dict[str, number]`. Old persisted floats
can't be deserialized as dict. Two options:
- **Drop old values**: on first startup with new code, old persisted keys
  are silently ignored. The View tab reader repopulates on next seed, and
  the PATCH endpoint handles edits going forward.
- **Alias**: if the old key exists, read the float, wrap as `{"Ashby": val}`,
  push into the new node, delete old key.

Either way, no full DB rebuild is needed. Old values for other nodes
(price, SD, commute, etc.) remain valid.

---

## Changes

### 1. Person — add financial fields

`houses/model/domain.py`:
```python
@dataclass(frozen=True)
class Person:
    ...
    home_sale_price: Money = Money("0", "GBP")
    outstanding_mortgage: Money = Money("0", "GBP")
    cash_contribution: Money = Money("0", "GBP")
    works_estimate_required: bool = False
```

Remove `deposit_equity` field from `Person` **and all callers**:
- `houses/nodes/settings.py`: remove from `make_default_persons()`
- `houses/nodes/monthly_mortgage_payment_node.py`: remove references (node is
  restructured, no longer reads `persons_source`)
- `houses/web/api_router.py`: remove `deposit_equity` validation in
  `patch_persons()`

Renamed in `PropertyNodes`: `comment_ashby_works` → `works_estimate`
(node ID: `f"{rid}/ashby_works"` → `f"{rid}/works_estimate"`).
The DAG has no person names in it.

### 2. Bootstrap — map sheet to Person fields

On bootstrap, Constants tab values are pushed onto Person objects:

| Constants tab | Person field |
|---|---|
| `Current Sale Price (£)` | Simon.`home_sale_price` |
| `Outstanding Mortgage (£)` | Simon.`outstanding_mortgage` |
| `Gross Ashby Contribution (£)` | Ashby.`cash_contribution` |

**Works estimate** stays on the `works_estimate` per-property node (not
on Person). Bootstrap must also read from the **View tab** to import
existing column values — add a View tab reader and merge step in
`load_property_nodes_from_rows()`. See Migration strategy above.

### 3. New: TotalWorksNode

`houses/nodes/total_works_node.py`

Sums `works_estimates` dict. Gates: if any non-child person has
`works_estimate_required=True` and is missing from dict → impossible.

```python
class TotalWorksNode(DerivedNode[Money]):
    """Total works for this property. Gates on required persons."""

    deps: persons_source, works_estimates_node

    def compute(persons, works_ests):
        ps = persons.value or []
        buyers = [p for p in ps if not p.is_child]
        wd = works_ests.value if works_ests.value is not None else {}

        missing = [p for p in buyers if p.works_estimate_required
                   and p.name not in wd]
        if missing:
            return Attempt.impossible(
                "Works estimate required for: " + ", ".join(p.name for p in missing))

        total = sum(Decimal(str(v)) for v in wd.values())
        return Attempt.succeeded(Money(str(total), "GBP"))
```

### 4. New: EquityTotalNode

`houses/nodes/equity_total_node.py`

Computes each person's total equity from their granular fields and sums.
All computation lives in DAG nodes, not on data objects.

```python
class EquityTotalNode(DerivedNode[Money]):
    """Σ (max(0, sale − mortgage) + cash) across all persons"""

    deps: persons_source

    def compute(persons):
        Z = Money("0", "GBP")
        ps = persons.value or []
        total = sum(
            max(ZERO, (p.home_sale_price or Z).amount − (p.outstanding_mortgage or Z).amount)
            + (p.cash_contribution or Z).amount
            for p in ps
        )
        return Attempt.succeeded(Money(str(total), "GBP"))
```

### 5. New: MortgageRequiredNode

`houses/nodes/mortgage_required_node.py`

Pure formula — no inline business logic. Depends on the aggregate nodes:

```python
class MortgageRequiredNode(DerivedNode[Money]):
    """Mortgage = Price + SD + TotalWorks − TotalEquity"""

    deps: rightmove_price, stamp_duty, total_works_node, total_equity_node

    def compute(price, sd, tw, te):
        if tw.impossible:
            return Attempt.impossible(tw.error)
        if te.impossible:
            return Attempt.impossible(te.error)

        p  = Decimal(price.value.amount)  if price.succeeded and price.value else ZERO
        sdv = Decimal(sd.value.amount)     if sd.succeeded and sd.value else ZERO
        w  = Decimal(tw.value.amount)     if tw.succeeded else ZERO
        e  = Decimal(te.value.amount)     if te.succeeded else ZERO

        return Attempt.succeeded(Money(str(max(ZERO, p + sdv + w − e)), "GBP"))
```

### 6. TotalMonthlyHousingCostNode — propagate impossible

The existing `TotalMonthlyHousingCostNode.compute()` silently skips
impossible deps:
```python
# Current (broken):
if mortgage.succeeded:
    total += mortgage.value_or_none()  # skips impossible → misleading total

# Fixed:
def compute(..., mortgage_att, ...):
    if mortgage_att.impossible:
        return Attempt.impossible(mortgage_att.error)
```
Add `mortgage_required` to the impossible check — if mortgage is impossible,
total is impossible.

### 7. Restructure MonthlyMortgagePaymentNode

- **Remove** `persons_source`, `rightmove_price`, `stamp_duty_node`, `equity_total` params
- **Add** `mortgage_required_node` param
- `compute()` becomes pure PMT on mortgage_required principal

```python
class MonthlyMortgagePaymentNode(DerivedNode[Money]):
    deps: mortgage_required_node, financial_source
```

### 6. Wire in PropertyNodes

```python
self.total_works = TotalWorksNode(
    f"{rid}/total_works",
    persons_source=self._svc.persons_source,
    works_estimates_node=self.works_estimates,
)
self.total_equity = EquityTotalNode(
    f"{rid}/total_equity",
    persons_source=self._svc.persons_source,
)
self.mortgage_required = MortgageRequiredNode(
    f"{rid}/mortgage_required",
    rightmove_price=self.rightmove_price,
    stamp_duty=self.stamp_duty,
    total_works_node=self.total_works,
    total_equity_node=self.total_equity,
)
self.monthly_mortgage = MonthlyMortgagePaymentNode(
    f"{rid}/monthly_mortgage",
    mortgage_required_node=self.mortgage_required,
    financial_source=self._svc.financial_source,
)
```

Add all new nodes to the signal wiring list.

### 8. API — move works_estimates to affordability

`to_json_detail()`:
- Remove `"ashby_works_estimate"` from `comments` (old key)
- Add `"works_estimates"` to `affordability` (from `self.works_estimates.to_json()`)
- Add `"total_works"` to `affordability` (from `self.total_works.to_json()`)
- Add `"total_equity"` to `affordability` (from `self.total_equity.to_json()`)
- Add `"mortgage_required"` to `affordability` (from `self.mortgage_required.to_json()`)

### 9. API — PATCH endpoint

```
PATCH /api/properties/{rid}/works-estimate  {"person": "Ashby", "value": 15000}
```
- Updates `works_estimates` dict for the given person on this property
- Writes back to View tab column "Ashby Works Estimate" synchronously
  via gspread. If the sheet write fails, log the error but return 200
  (DAG is source of truth; eventual consistency with sheet).

### 10. Frontend — type updates

`PropertyDetail`:
- Remove `ashby_works_estimate` from `comments` (old key)
- Add `works_estimates: AttemptValue<Record<string, number>>` to `affordability` (dict)
- Add `total_works: AttemptValue<MoneyValue>` to `affordability`
- Add `total_equity: AttemptValue<MoneyValue>` to `affordability`
- Add `mortgage_required: AttemptValue<MoneyValue>` to `affordability`

### 11. Frontend — CostsSection row

Add row between Sinking Fund and Commute Cost, showing **total** works:
```
Cost of Works    £20,000  [edit] [ⓘ breakdown]
```
- Click-to-edit on the value → inline `<input type="number">` with £ prefix
- Saves on blur/Enter, cancels on Escape
- Unset state: "£? — required", styled as warning
- Provenance toggle shows the cash breakdown (SD split + works) and per-person attribution

### 12. Frontend — Per-person works display

The detail page has `settings.persons` (with `works_estimate_required`)
and `affordability.total_works` / `affordability.works_estimates` (dict).
If it has data, show the per-person breakdown from the dict. If pending but
someone requires it, show "£? — required".

### 13. Frontend — Impossible state per row

When works estimate missing, the affordability chain becomes impossible.
Every cost row in `CostsSection.vue` that depends on it must render
`impossible` distinctively:

| Row | Current render | Fixed render |
|---|---|---|
| Cost of Works | `£?` (no check) | `"£? — required"`, grey text, warning icon |
| Mortgage | shows amount | `"Impossible"`, grey text, muted row |
| Total Monthly | shows amount | `"Impossible"`, grey text, muted row |

Each row must check `AttemptValue.succeeded`. If false → "Impossible" style.
Existing rows (Council Tax, Sinking Fund, Commute) remain unchanged — they
compute independently.

---

## Tests

### Unit: TotalWorksNode

| Test | Persons | Works dict | Expects |
|---|---|---|---|
| Required person missing | Ashby(works_req=True) | `None` | `impossible("Works estimate required for: Ashby")` |
| No one requires, dict pending | All(works_req=False) | `None` | `succeeded(0)` |
| Empty dict (not None) | All buyers present | `{}` | `succeeded(0)` — same as None |
| Dict has values | All buyers present | `{"Ashby": 20000, "Simon": 5000}` | `succeeded(25000)` |
| Dict with zeros | All buyers present | `{"Ashby": 0}` | `succeeded(0)` |
| Some required, some missing | Simon(works_req=True), Ashby(works_req=False) | `{"Ashby": 5000}` | `impossible("required for: Simon")` |

### Unit: EquityTotalNode

| Test | Persons | Expects |
|---|---|---|
| Single person, home sale only | Person(home_sale=500k) | `500k` |
| Single person, cash only | Person(cash=100k) | `100k` |
| Home with equity via partial mortgage | Person(home_sale=500k, mortgage=200k) | `300k` |
| **Negative equity floored at 0** | Person(home_sale=200k, mortgage=300k) | `0` |
| Both home and cash on same person | Person(home_sale=200k, cash=50k) | `250k` |
| Cross-person sum | A(home=300k,mortgage=100k), B(cash=200k) | `200k + 200k = 400k` |
| All defaults | Person() | `0` |

> Note: the negative equity test (`max(0, 200k−300k)`) validates the floor.
> A no-op implementation returning 0 would fail this test because the
> expected result for `max(0, 200k−300k) + 0 = 0` happens to equal 0.
> Pair it with the `home=500k, mortgage=200k` case in the same test
> function to prove computation happens.

### Unit: MortgageRequiredNode

| Test | Inputs | Expects |
|---|---|---|
| Standard | price=500k, sd=15k, tw=20k, te=477k | `58k` |
| No works | price=500k, sd=15k, tw=0, te=477k | `38k` |
| No equity | price=500k, sd=15k, tw=20k, te=0 | `535k` |
| All zeros | price=0, sd=0, tw=0, te=0 | `0` |
| total_works impossible | tw impossible | propagates error |
| total_equity impossible | te impossible | propagates error |

### Unit: MonthlyMortgagePaymentNode (restructured)

| Test | Inputs | Expects |
|---|---|---|
| Zero principal | mortgage_required=0, rate=0.045, term=30 | `succeeded(0)` |
| Standard PMT | mortgage_required=235000, rate=0.0495, term=27 | `≈£1,241.57` |
| No longer reads persons_source | — | compiles without Person ref |

### Integration: impossible propagation chain

Create full DAG with `Ashby(works_estimate_required=True)` and
`works_estimates=None` (never pushed). Assert all downstream:
- `total_works` → impossible (message contains "Ashby")
- `mortgage_required` → impossible (propagated)
- `monthly_mortgage` → impossible (propagated)
- `total_monthly_cost` → impossible (propagated)

### Integration: wiring topology

Construct `PropertyNodes` for test RID, verify dependency graph:
- `prop.mortgage_required._deps` includes `prop.total_works` and `prop.total_equity`
- `prop.monthly_mortgage._deps` includes `prop.mortgage_required`
- `prop.monthly_mortgage._deps` does NOT include `prop._svc.persons_source`
- `prop.total_works._deps` includes `prop.works_estimates`

### Unit: MonthlyMortgagePaymentNode

| Test | Expects |
|---|---|
| Takes mortgage_required input, not persons_source | Compiles without Person refs |
| PMT on zero principal → zero | succeeded(0) |

---

## TDD Sequence

1. RED — `TotalWorksNode`: required person missing → impossible
2. GREEN — implement node
3. RED — `TotalWorksNode`: no one requires, dict pending → succeeded(0)
4. GREEN — pass
5. RED — `TotalWorksNode`: empty dict `{}` → succeeded(0)
6. GREEN — pass
7. RED — `TotalWorksNode`: dict with values → correct sum
8. GREEN — pass
9. RED — `EquityTotalNode`: home sale with equity (500k−200k) → 300k
10. GREEN — implement node
11. RED — `EquityTotalNode`: negative equity (200k−300k) → 0
12. GREEN — pass
13. RED — `EquityTotalNode`: home + cash on same person → sum
14. GREEN — pass
15. RED — `EquityTotalNode`: cross-person sum → aggregate
16. GREEN — pass
17. RED — `MortgageRequiredNode`: standard → correct formula
18. GREEN — implement node
19. RED — `MortgageRequiredNode`: all zeros → 0
20. GREEN — pass
21. RED — `MortgageRequiredNode`: tw impossible → propagates
22. GREEN — pass
23. RED — `MonthlyMortgagePaymentNode`: standard PMT → correct value
24. GREEN — restructure node
25. RED — `MonthlyMortgagePaymentNode`: no persons_source dep
26. GREEN — update callers
27. RED — `TotalMonthlyHousingCostNode`: mortgage impossible → total impossible
28. GREEN — add propagation check
29. RED — integration: impossible propagation chain (4 nodes)
30. GREEN — wiring test passes
31. RED — integration: wiring topology verification
32. GREEN — dependencies match expected graph
33. RED — detail API has `works_estimates` + `total_works` + `total_equity` + `mortgage_required`
34. GREEN — move fields in `to_json_detail`
35. RED — PATCH `/api/properties/{rid}/works-estimate`
36. GREEN — implement endpoint
37+. Frontend: types → Costs row per-person breakdown → impossible per row
