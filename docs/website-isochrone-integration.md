# Website Isochrone Integration — Plan

Status: plan (2026-08-03). Audience: developers implementing this feature.
Toolchain mechanics (stages, engines, artifacts, tolerances) live in
[docs/rightmove-commute-monitor.md](rightmove-commute-monitor.md) — this doc
covers only the website integration and links there instead of repeating it.

## Goal

Make the isochrones a first-class website feature:

1. A **family settings page** — every person's commutes on one page; each
   person edits only their own (server-enforced); kids have no isochrones by
   default but can have a commute (e.g. other parent's house).
2. A **generate affordance** — triggers the isochrone batch, with an honest
   warning that it is slow and consumes free-API allocation.
3. A **view affordance** — opens the combined map with per-commute layers and
   the all-commutes intersection.
4. Settings are the **single source of truth** for destinations and
   thresholds — the toolchain stops being hand-configured.

## Current state (facts, not plan)

- Toolchain: CLI batches in `tools/commute/` → committed artifacts in
  `data/commute/` (station_shed.json, drive_isochrone.json, drive_searches.json,
  intersection.json, commute_map.html). Resumable, atomic writes, config-signature
  reuse guards. Engine detail in [rightmove-commute-monitor.md](rightmove-commute-monitor.md).
- App: FastAPI + Vue SPA (hash router; views: List, Detail, Login — **no settings
  page yet**). `GET /api/settings` returns persons/financial/thresholds;
  `PUT /api/settings/persons` replaces the whole list **with no per-person
  authorization** — a gap this plan closes.
- Identity: Google sign-in cookie `{email, name, is_superuser}`; person = the
  `Person` whose email matches. Auth middleware on `/api`.
- Persons: Simon (Pimlico-transit, Bracknell-drive, Dad-drive), Lorena
  (Aldgate-transit), Ashby (no POIs), George (`is_child=True`, school POIs with
  **empty addresses** — resolved per-property, never geocoded).
- Background: `dag/scheduler.py` refreshes stale DAG nodes — **not** a general
  job runner; a 50-minute batch does not belong there.
- `data/commute/commute_map.html` is fully self-contained; already served on the
  LAN by `make commute-serve` (port 8123).

## UX design

### Family settings page (`/settings`)

One scrollable page, one section per person in configured order. Rationale: the
household buys together (personas: "pointless if anyone's doesn't work"), so
everyone's commutes are everyone's context; ownership is a per-field concern, not
a page concern.

Per-person section:

| Element | Own person | Other people |
|---|---|---|
| Header (name, avatar, "you" badge, child badge) | — | — |
| Car access, financials, commute thresholds | editable | read-only, locked |
| POI list (label, address, mode, trips/week) | editable, add/remove | read-only |
| Isochrone mode per POI (transit / drive / none) | editable | read-only |
| "Included in isochrone map" badge per POI | — | — |

Rules:

- **Never trust the UI for ownership.** The server rejects edits to another
  person's record (`PATCH` scoped to the session person), even if the UI hid the
  controls.
- **Kids are a special case.** A child has no login, so `editable_by` on the
  `Person` names the adults who may edit them (superuser always allowed). Default
  for a child: all adults. George's school POIs have empty addresses → mode
  `none`, no isochrone; this is the default child state, shown as "goes to
  school near the house".
- **A child CAN get an isochrone**: adding a real POI (e.g. "Other parent",
  OX postcode, drive mode) includes it in the drive shed and the intersection.
  The UI must surface the consequence: "adding this commute shrinks the
  all-commutes area" (see Intersection semantics).

### Isochrones panel (on the settings page, below the people)

Per-stage status cards with a shared state machine:

| State | Meaning | UI |
|---|---|---|
| `missing` | never generated | "Generate" button |
| `stale` | settings changed since last run (signature mismatch) | "Regenerate", amber badge "out of date" |
| `generating` | batch running | spinner, elapsed time, "runs in the background — you can leave" |
| `ready` | current | green, `generated_at`, "View map" enabled |
| `failed` | stage errored | red, log tail, "Retry" (stages are resumable) |
| `interrupted` | server restarted mid-batch | "Resume" (toolchain resumes from its checkpoint) |

Affordances:

- **Generate / Regenerate** → confirmation dialog stating the real cost:
  - First run ≈ **50 min** (routes ~2,600 stations via TfL — free API, no quota,
    one-off; ~12 OpenRouteService matrix calls per drive regeneration — free
    tier: **500 calls/day, non-commercial single-user**).
  - Re-runs are fast: only stale stages run (config-signature diff), everything
    else is served from the committed artifacts.
  - Dialog must not be dismissible-by-Enter-once-submitted; the job runs to
    completion in the background and the page updates via WebSocket.
- **View map** → `#/settings` → opens `/commute/commute_map.html` (new tab):
  per-commute layers (transit, each drive) + the gold intersection layer.
- Per-commute deep link (stretch): `commute_map.html?layer=Dad` preselects one
  layer — the map generator accepts the query param.
- **Generate is superuser-only.** The rest of the family can view status and the
  map. Rationale: generation burns free-API allocation and takes the machine.

## Architecture

### The toolchain stays out-of-process

Never port the batch into the app. It is a 50-minute, thousands-of-calls,
quota-consuming, committed-artifact pipeline; the DAG scheduler is for
per-property refreshes. The app **orchestrates**:

```
POST /api/isochrones/generate
  └─ houses/isochrones.py (new)
       ├─ derive config from settings → write toolchain inputs
       ├─ stage dependency order, skip stages whose config signature matches
       ├─ asyncio.create_subprocess_exec(python -m tools.commute.<stage>)
       ├─ write data/commute/generation.json (runtime state, gitignored)
       └─ on completion: WebSocket event "isochrone-status"
```

Rules:

- **One generation at a time.** Concurrent `POST /generate` → 409 while a job is
  running (lock in `generation.json`).
- **Never kill a running stage.** The toolchain is resumable and atomic-writes;
  an interrupted batch shows `interrupted` and resumes. Document: don't restart
  the server mid-batch (uvicorn `--reload` orphans the child; the status model
  tolerates it).
- **Serve, don't gate.** `app.mount("/commute", StaticFiles(directory="data/commute"))`
  serves the map and payloads read-only. Atomic writes make mid-generation reads
  safe (readers get the old or new file, never a partial). `make commute-serve`
  (port 8123) stays for phone viewing without the app running.

### Status model

`data/commute/generation.json` (runtime, gitignored):

```json
{
  "state": "generating",           // running | ready | failed | interrupted
  "started_at": "...", "finished_at": null,
  "settings_signature": "sha256",  // persons+POIs+thresholds hash
  "stages": [
    {"name": "transit-shed", "state": "ready", "exit_code": 0, "generated_at": "..."},
    {"name": "drive",        "state": "generating", ...},
    {"name": "intersection", "state": "pending", ...},
    {"name": "map",          "state": "pending", ...}
  ]
}
```

Readiness of an artifact = its committed metadata's config signature matches the
current `settings_signature` (same mechanism the toolchain already uses for its
reuse guards). `GET /api/isochrones/status` computes this for every stage.

### Settings-derived isochrone config (kills the hard-coding)

| Today (hard-coded) | Becomes |
|---|---|
| Transit offices = `simon_destination`/`lorena_destination` in `houses/config.py` | POIs with `isochrone_mode=transit` |
| `data/commute/drive_destinations.json` (hand-maintained) | **generated** from POIs with `isochrone_mode=drive` |
| Thresholds 132/90 min | new `isochrone` settings block: `{transit_min: 132, drive_min: 90}`, per-POI override |
| Kids excluded implicitly | school POIs (empty address) → mode `none`; a kid's real POI participates |

Data model changes:

- `PlaceOfInterest.isochrone_mode: "transit" | "drive" | "none" | None` —
  `None` on read means "unset": migrated by rule (Pimlico/Aldgate → transit,
  Bracknell/Dad → drive, school → none), explicit thereafter. **Never infer
  mode at generation time** — the app's per-property selector (walk→transit→
  drive) is per-property, not per-destination; isochrones need a fixed mode.
- `Person.is_child` exists; add `Person.editable_by: list[str]` (default `[self]`,
  superuser implied; child default = all adults).
- `stations.csv`/TfL destinations: `station_shed.py` gains a `--config` input
  (destinations + threshold) instead of reading `houses/config.py` — the app's
  derived config is the new source.

### API surface

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /api/settings` | any | unchanged; persons now carry `editable_by_me`/`isochrone_mode` |
| `PATCH /api/settings/person/{name}` | **own person / superuser / in `editable_by`** | replace `PUT /settings/persons` (removes the whole-list, no-authz endpoint) |
| `GET /api/isochrones/status` | any authenticated | per-stage state + staleness + links |
| `POST /api/isochrones/generate` | superuser | start/resume the batch; 409 while running |
| `GET /commute/*` | any (app behind auth) | static artifacts (map HTML, payloads) |
| WebSocket `isochrone-status` event | session | live updates via existing `/api/ws` + `useWebSocket` |

## Phases

### Phase 1 — settings page + authorization

1. `Person.editable_by`, `PlaceOfInterest.isochrone_mode`; migration of existing
   settings; `PATCH /api/settings/person/{name}` with server-side ownership;
   remove `PUT /api/settings/persons`.
2. `SettingsView.vue` + `/settings` route + header link: family sections,
   own-vs-other rendering, child styling + school note, POI editor with mode.
3. Tests: authz matrix (own/other/superuser/guardian), migration mapping,
   frontend component tests (read-only others, editable own, child note).

### Phase 2 — generation + map

4. `houses/isochrones.py`: config derivation, stage orchestration, status file,
   WebSocket event. `station_shed.py` `--config`.
5. Endpoints `GET /status`, `POST /generate` (409, superuser), static mount.
6. Isochrones panel UI: state machine, cost dialog, view-map button.
7. Tests: derivation (settings→destinations+thresholds), status transitions
   (with a fake `tools.commute` subprocess), 409 concurrency, e2e render
   (settings page + map link).

### Phase 3 — polish

8. `?layer=` deep links; per-POI "included in map" badges; empty-intersection
   callout ("no house satisfies every commute — relax a threshold or a POI").

## Decisions & risks

| Decision | Rationale |
|---|---|
| Batch stays a subprocess, not a DAG node | 50-min job, thousands of one-off API calls, committed-artifact pipeline; the DAG scheduler is per-property refresh |
| Whole-family page, read-only others | Buying together → everyone's commutes are everyone's context; ownership is per-field, enforced server-side |
| Generate = superuser-only | Quota burn + machine load; rest of family reads status and the map |
| Strict intersection (AND of every drive POI, incl. kids') | "Where we can buy" must satisfy everyone; UI shows which POIs feed it so a shrinking area is explainable; empty state handled explicitly |
| `isochrone_mode` explicit, never inferred | The app's per-property mode selector cannot decide a region's mode |
| Runtime state in `generation.json`, artifacts stay committed | Reproducibility/reviewability of artifacts unchanged; runtime state is ephemeral |
| **Risk: 50-min first run vs uvicorn `--reload`** | Status model tolerates `interrupted`; toolchain resumes from checkpoint; document "don't restart mid-batch" |
| **Risk: ORS free tier (500/day, non-commercial)** | Batch uses ~12 calls; regeneration only on settings change; warn in the dialog |

## Acceptance

1. `PATCH /api/settings/person/{name}` for another person → 403; own → 200;
   guardian edits child → 200. `PUT /api/settings/persons` gone.
2. Editing a POI mode/address/threshold flips the isochrone panel to `stale`.
3. `POST /api/isochrones/generate` (superuser) runs only stale stages, writes
   committed artifacts + `generation.json`, pushes a WebSocket event; concurrent
   POST → 409; non-superuser → 403.
4. `GET /commute/commute_map.html` renders the map with per-commute layers and
   the intersection layer.
5. `make test` green; new unit + e2e coverage per phase.
