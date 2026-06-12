# Commute Breakdown

**Module**: `houses/enricher.py` — `compute_commute_breakdown()`

**Purpose**: Combine individual commute costs into a single yearly total for the commute cost formula.

**How it works:**
1. Reads daily costs from Simon's `Commute`, Lorena's `Commute`, and Bracknell's `Commute`.
2. Calculates yearly total:
```
yearly_total = working_weeks × (bracknell_daily_cost + simon_daily_cost + lorena_daily_cost × weekly_lorena_trips)
```

**Default values (configurable in config.py):**
- `working_weeks_per_year`: 46
- `weekly_lorena_trips`: 2
- `weekly_simon_trips`: 1
- `weekly_bracknell_trips`: 1

**Returns:** `CommuteBreakdown` dataclass with individual daily costs, yearly total, and a human-readable formula string.

**How it's used:** The yearly total feeds into the View tab's "Monthly Commute Cost (£)" formula via XLOOKUP from the Properties Data tab. The formula is also displayed alongside the total for transparency.

**Graceful degradation:** If any daily cost is `None`, the yearly total is `None` — no partial estimate is produced.
