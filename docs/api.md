# API Reference

All endpoints live on port 8765 (`make run`). **The source of truth is the running server's auto-docs at `/docs`** (FastAPI generates them from `houses/web/api_router.py`). This page records the non-obvious behaviour — query-param semantics and response shapes — that the code doesn't explain by itself.

| Endpoint | What's non-obvious |
|----------|--------------------|
| `GET /properties`, `GET /properties/{rid}` | **Require `?tab=view` or `?tab=data`** — missing tab is an error |
| `GET /properties/{rid}` | Detects duplicate RIDs → `409 Conflict` |
| `POST /properties` | Single-property upsert **or** batch refresh (see below) |
| `POST /properties/compare` | No-write re-enrich → TSV diff of sheet vs fresh values |
| `POST /sync-view-formulas` | Refresh View tab XLOOKUPs after column changes; idempotent |

## POST /properties — batch refresh

With `rids`: reads View tab, matches to Data tab, enriches, writes back. Output is streamed newline-delimited JSON, **always ending with**:

```json
{"type": "summary", "updated": 40, "skipped": 1, "created": 0, "errors": 0}
```

Query params:

| Param | Type | Default | Semantics |
|-------|------|---------|-----------|
| `fields` | list | all | enrichment groups: `simon,lorena,petrol,schools,walk_time,amenities,town,epc,council_tax,geo` |
| `rids` | str | all | comma-separated RIDs |
| `force` | bool | false | `true` = overwrite existing cells; `false` = fill blanks only |
| `no_write` | bool | false | enrich without writing to the sheet |

Example — force refresh Simon/Lorena for specific rows: `curl -X POST "http://localhost:8765/properties?fields=simon,lorena&force=true&rids=88275093,173431283"`.

## POST /properties/compare

No-write re-enrich of every property → TSV diff: `RID  Field  Old (sheet)  New (enriched)`. Used to verify refactoring didn't change output. Params: `rids`, `fields`.

## Troubleshooting

Batch endpoints fail in non-obvious ways (streaming, `--reload` kills, `force` propagation) — see `docs/troubleshooting-endpoints.md`.
