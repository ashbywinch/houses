# Bus Fare Data Pipeline

Bus fare data comes from the **BODS** (Bus Open Data Service) NeTEx fare
datasets. The extraction script downloads fare data for commuter-belt
operators, parses the NeTEx XML, and produces `data/bus_fares.json`.

## Usage

```bash
# Extract bus fares from cached BODS files (fast, uses existing cache)
uv run python scripts/extract_bus_fares.py --cached-only

# Full from-scratch download and extraction
uv run python scripts/extract_bus_fares.py
```

**Flags:**
- `--cached-only` — read cached XML files from `data/bods_cache/` instead of
  re-downloading
- `--force` — ignore operator checkpoints and re-process everything

## What It Does

1. Downloads NaPTAN stop coordinates (cached to `data/bods_stops.csv`)
2. Queries BODS API for fare datasets by operator NOC
3. Filters datasets by sub-operator name (exact description match)
4. Downloads and parses NeTEx XML files (line fares, network passes, fare
   tables)
5. Extracts zone structures, stop→zone mappings, and zone pair prices
6. Accumulates network fares (day/return passes) and applies them across
   files
7. Writes per-operator checkpoints to `data/.bus_fares_checkpoints/`,
   merged into `data/bus_fares.json`

## Updating the Sheet

After re-extracting fares:

1. Re-run extraction: `uv run python scripts/extract_bus_fares.py --cached-only`
2. The server picks up the new `bus_fares.json` on next restart.
3. Trigger a batch refresh for affected properties:

```bash
curl -X POST "http://localhost:8080/properties?fields=simon,lorena&force=true"
```
