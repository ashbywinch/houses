# API Reference

All endpoints live on port 8765 (`make run`). **The source of truth is the running server's auto-docs at `/docs`** (FastAPI generates them from the routers). This page records the non-obvious behaviour that the code doesn't explain by itself.

| Endpoint | What's non-obvious |
|----------|--------------------|
| `GET /properties` | Lists every property from the DB-backed DAG registry. Legacy `?tab=view\|data` param is accepted and ignored — both serve the same rows |
| `POST /api/properties` | Single-property upsert (scrape a Rightmove URL); re-adds of an existing RID are rejected unless `fields=` is passed |
| `POST /api/admin/regenerate` | Superuser-only force-recompute of derived nodes matching id patterns |

## POST /api/properties

Single-property mode: JSON body `{url, address?, postcode?, bedrooms?, price?}`. Always scrapes/enriches and seeds the DAG; responds with `{status, rid, data}`.

Duplicates: the **database** is the source of truth. Re-posting a RID that already has DAG rows returns `400 {"error": "Property <rid> already exists. Use fields= to re-enrich specific fields."}`. Passing `fields=` bypasses the rejection.

Legacy contract: a call with **no JSON body** answers `200 null` — it used to be the sheet batch-refresh entry point and is kept as a no-op so old callers don't error. The old batch query params (`rids`, `force`, `no_write`) are accepted but inert.

## POST /api/admin/regenerate

Superuser-only. Body: `{"patterns": ["*/council_tax"]}` where `*` matches any run of characters (a pattern without `*` is an exact node id). Matched input nodes have no computation and are reported as skipped; dependents cascade because the scheduler is drained before responding. See `houses/admin_router.py`.

## Troubleshooting

Long requests die unobviously when `uvicorn --reload` restarts mid-flight — see `docs/troubleshooting-endpoints.md`.
