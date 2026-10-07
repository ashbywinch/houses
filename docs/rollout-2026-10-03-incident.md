# Rollout incident 2026-10-03 — ORS 403 on the recovery box, and what it hid

Context: the recover→flip recovery rollout. The smoke/review box was freshly
installed (replace-not-repair); its convergence sweep's ORS calls were refused
and the reviewer saw `Client error '403 Forbidden' for url
'https://api.openrouteservice.org/v2/directions/driving-car'` on the review
surface.

> UPDATE 2026-10-05 (resolved): the 403s were **not** an overuse bug and no
> quota was burned. The app was calling the **deprecated** host
> `api.openrouteservice.org` — deprecated 2026-04-28, limited to **10% of the
> plan's quota** from 2026-08-27, and its usage is deliberately absent from the
> account dashboard (which reports only the new host's quota). See Bug 1.
>
> UPDATE 2026-10-05: the same week surfaced a second, unrelated defect class —
> persisted `impossible` results survive the fix that makes them wrong, because
> they are terminal for the DAG and the code fingerprint only covers a node's
> own code. That is recorded below as Bug 4, because it is what turned a stale
> row into "the monthly deltas have gone missing" on the review surface.

Bug log — each entry: the symptom, the evidence, the root cause, the fix.

---

## Bug 1 — we were calling the deprecated HeiGIT host (RESOLVED)

**Symptom:** every ORS *directions* call failed with
`403 {"error": "Quota exceeded"}` — while the account dashboard showed
Directions V2 at 2000/2000 and matrix/isochrones/geocoding on the same key kept
returning 200.

**Root cause:** the code called `api.openrouteservice.org`, deprecated by
HeiGIT on 2026-04-28 in favour of `api.heigit.org/<service>/<version>/`. From
2026-08-27 the deprecated host carries 10% of the plan's quota, and — this is
what made the failure unattributable — *its usage is not shown in the
dashboard*: the dashboard reports the new host's quota only.

So the dashboard's "2000/2000" was the **new** host, which we never called,
while the **old** host's reduced pool was the one being refused.

**Evidence that it was not overuse:** the LAN's ORS request log (284 directions
responses, Jun–Sep, read from the response cache's `metadata.query`) is one
request per leg with no repeats (277 of 284 distinct at ~1 m rounding); the
owner cached 12 directions successes in its first 20 minutes and then stopped;
the standby's sweep made at most 3 attempts (one per process — the quota flag
short-circuits the rest).

**Fix:** migrated every HeiGIT URL to the documented structure, with one owner
for the URLs (`houses/ors_endpoints.py`) and the host as a setting
(`HOUSES_ORS_BASE_URL`). Geocoding moved under its own service prefix
(`/pelias/v1/...`). Also fixed: `ORSApi.reverse_geocode` never sent the
`Authorization` header (401), and the walkability town-centre lookup sent no
Pelias `layers`, so it resolved to the house's own street (a 0-minute walk the
plausibility gate then rejected → `walk_to_town: null`).

---

## Bug 2 — the retry mechanism waits for the wrong reset time (OPEN)

**Symptom:** after the 403 the gate short-circuits until **UTC midnight**
(`ApiGate._next_utc_midnight`), but ORS's daily quota does not reset at UTC
midnight — it resets **24 hours after the window's first request** (per-token
anchor; see ask.openrouteservice.org/t/quota-reset-server-timezone/58 and
/t/rate-limit-exceeded-how-does-it-work/5067, incl. the ~2000/day figure).
So every post-midnight retry lands a real 403 again and re-arms to the next
midnight: the error can never clear on its own.

**Root cause:** `ApiProfile` assumes a calendar-midnight reset for ORS; the
403 response body (`"Quota exceeded"` vs auth errors) is ignored, so a dead
key would also retry forever.

**Fix (tracking):** per-profile reset policy with ORS's true semantics (24 h
from the window's first request), single retry at the real boundary — **not**
periodic probing (a request inside the window changes nothing). Classify the
403 body: recoverable quota vs permanent key problem. With ~40 properties and
a warm cache the quota should not be reachable; if it still is, the demand
question is separate.

---

## Bug 3 — the API cache is not carried across a rollout (and is wiped by it) (OPEN)

**Symptom:** every fresh install starts with an empty response cache and
re-fetches every route live.

**Evidence:** the cache lives under the app checkout —
`/opt/houses/app/data/api_cache` (the app's CWD-relative default) — and
`install-artifact.sh` replaces that whole tree on every rollout. `/opt/houses/data/`
(the stable data dir the bootstrap creates) has no `api_cache`.

**Root cause:** cache path is inside the replace-not-repair tree; the
seed/restore object carries only the DB (no cache).

**Fix (tracking):** move the cache to a stable box path the install never
touches (e.g. `/opt/houses/data/api_cache`, configurable), and seed it from
the live owner during rollout prep so a fresh box serves warm.

---

## Bug 4 — a fixed bug's persisted rows survive it (FOUND 2026-10-05)

**Symptom:** the review box showed September's failures for days after their
cause was fixed: `walkability: impossible` rows carrying the old host's 401,
and — because `settings/current_home` was `impossible` with the same stale
`dep_failed` chain — **no `monthly_baseline`, so every `delta_vs_home` was
null and the index showed plain totals instead of "the change vs your home"**.
The delta code was intact the whole time.

**Root cause:** two independent gaps.

1. `impossible` is **terminal** for the DAG: nothing re-derives a row whose
   inputs later became healthy unless a dependency write signals the node.
2. The code fingerprint (`code_version`) covers a node's **own** compute code.
   The fixes that mattered here (the ORS host, the missing auth header, the
   missing geocoding `layers`) live in the API layer, so no node looked
   stale-in-code and the deploy's sweep re-queued nothing. Worse, a later
   sweep **re-propagated** the stale error text into fresh rows (214 rows
   rewritten with September's message).

A third, structural gap surfaced with it: `settings/current_home` is a
settings node, so a property node's write never signalled it. Its deps now
include the current home's `group_monthly_cost` and `best_address` as real
signal edges, so a re-price re-derives the baseline.

**Measuring it:** a substring search over `node_results.result_json` finds
nothing — the column is zlib, so rows must be inflated to search them (the
substring trap cost real time here). 1,727 rows still carried the deprecated
host's refusal when we looked, spread across the whole affordability chain.

**Operational fix (used, and required after every API-layer fix):** regenerate
the poisoned roots and let the cascade rebuild the chain, via the superuser
endpoint:

```
POST /api/admin/regenerate {"patterns": ["*/walkability", "*/park_and_ride"]}
POST /api/admin/regenerate {"patterns": ["settings/current_home", "*/delta_vs_home"]}
```

`*/walkability` and `*/park_and_ride` are the ORS callers (the roots);
`settings/current_home` needs its own entry because settings nodes are not
downstream-triggered by property nodes; the deltas follow the baseline.

**Still open:** make an API-layer change invalidate the rows that depend on
it — either include the API-layer modules in the fingerprint, or make the
rollout's post-install step regenerate the known roots. Until then, shipping
an API-layer fix means remembering the regenerate.

---

## Later (open, discovered as we go)

- Whether Google Routes should be primary for the drive legs currently
  falling through to ORS.
- Whether the frontend should distinguish cache-cold recompute storms
  (transient) from genuinely failed legs in provenance.
- The API cache's poison check parses a whole cached body to read one status
  field (`api_cache.CachingTransport`) — a small projection opportunity.
