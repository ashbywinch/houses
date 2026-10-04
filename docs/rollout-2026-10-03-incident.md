# Rollout incident 2026-10-03 — ORS quota exhausted on the recovery box

Context: the recover→flip recovery rollout. The smoke/review box was freshly
installed (replace-not-repair) and its convergence sweep immediately consumed
the daily openrouteservice quota; the app then 403'd on every ORS call for the
rest of the window, and the reviewer saw `Client error '403 Forbidden' for url
'https://api.openrouteservice.org/v2/directions/driving-car'` on the review
surface.


> UPDATE 2026-10-04: Bug 1 and Bug 3 are SEPARATE defects. The cold cache
> (Bug 3) is real, but it is NOT established as the engine behind the quota
> burn — 40 properties over a few rollout attempts is far too small for the
> ~2000-request daily cap unless something multiplied every leg on every
> attempt. Root cause of Bug 1 is OPEN.
Bug log — add entries as the investigation continues. Each entry: the
symptom, the evidence, the root cause, the fix that lands.

---

## Bug 1 — ORS request overuse (thousands of requests for ~40 properties)

**Symptom:** the daily quota (ORS free tier ~2000 requests) was gone
minutes into the first sweep; `houses.apigw` logged
`daily quota exhausted — calls short-circuited until UTC midnight` at
**Evidence:** the gate flipped at 2026-10-03 18:32:17 (one 403 → exhausted
until UTC midnight); ORS still returned `403 {"error": "Quota exceeded"}`
at 2026-10-04 ~08:00 UTC — hours after midnight, so ORS's own window was
still exhausted. The standby's response cache (all providers) accumulated
1467 entries / 54 MB yesterday — an upper bound on total cached responses,
not an ORS-only count.

**Root cause: OPEN.** 40 properties × a few rollout attempts must not reach
the daily cap. Candidates, each unproven:

- other consumers share the key (the LAN dev app's sweeps, scripts, the
  scrape worker);
- ORS counts per-endpoint or the free-cap is lower than 2000 today, or
  403/5xx attempts count on ORS's side;
- a non-gated ORS caller (raw httpx) bypassing the quota flag.

**Needed evidence:** the ORS dashboard's usage count and the key's actual
reset time (authoritative for both Bug 1 and Bug 2); a per-provider count
of cache entries; journal evidence of which processes hit ORS when.

---

## Bug 2 — the retry mechanism waits for the wrong reset time

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

**Fix (tracking):** per-profile reset policy with ORS's true semantics
(24 h from the window's first request), single retry at the real boundary —
**not** periodic probing (a request inside the window changes nothing).
Classify the 403 body: recoverable quota vs permanent key problem. Note:
with only ~40 properties and a warm cache the quota should not be reachable;
if it still is, the demand question is separate.

---

## Bug 3 — the API cache is not carried across a rollout (and is wiped by it)

**Symptom:** every fresh install starts with an empty response cache and
re-fetches every route live.

**Evidence:** the cache lives under the app checkout —
`/opt/houses/app/data/api_cache` (the app's CWD-relative default) — and
`install-artifact.sh` replaces that whole tree on every rollout. Owner:
450 files / 25 MB (survived because the owner has not been reinstalled since
Oct 1). Standby: 1467 files / 54 MB, all rebuilt yesterday after each
install wiped the previous cache. `/opt/houses/data/` (the stable data dir
the bootstrap creates) has no `api_cache`.

**Root cause:** cache path is inside the replace-not-repair tree; the
seed/restore object carries only the DB (no cache).

**Fix (tracking):** move the cache to a stable box path the install never
touches (e.g. `/opt/houses/data/api_cache`, configurable), and seed it from
the live owner during rollout prep so a fresh box serves warm.

---

## Later (open, discovered as we go)

- Whether the ORS free key's quota is also consumed by the LAN dev app /
  scrape worker paths sharing the key.
- Whether Google Routes should be primary for the drive legs currently
  falling through to ORS.
- Whether the frontend should distinguish cache-cold recompute storms
  (transient) from genuinely failed legs in provenance.