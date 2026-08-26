# Bus Fare Data Pipeline

Bus fare data comes from **BODS** (Bus Open Data Service) NeTEx fare datasets. `scripts/extract_bus_fares.py` downloads commuter-belt operator fares, parses the NeTEx XML, and produces `data/bus_fares.json`. Flags and extraction steps: see the script.

## Non-obvious behaviour

- Downloads NaPTAN stop coordinates (cached to `data/bods_stops.csv`); filters datasets by sub-operator name (exact description match).
- Accumulates network fares (day/return passes) and applies them across files.
- Writes per-operator checkpoints to `data/.bus_fares_checkpoints/`, merged into `data/bus_fares.json` — so extraction is resumable, not from-scratch each time.

## Recomputing after re-extracting

1. Re-run extraction: `uv run python scripts/extract_bus_fares.py --cached-only`
2. Server picks up the new `bus_fares.json` on next restart.
3. Persisted commute nodes keep their old fares until regenerated — force a
   recompute with the superuser endpoint:

```bash
curl -X POST "http://localhost:8765/api/admin/regenerate" \
     -H "Content-Type: application/json" \
     -b "session=<admin session cookie>" \
     -d '{"patterns": ["*/bus_route", "*/bods_fare"]}'
```

Dependents (cheapest fare, monthly costs) cascade automatically — the
scheduler is drained before the endpoint responds.
