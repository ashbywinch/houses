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
5. The batch **survives server crashes and auto-resumes**; the page shows
   **live progress** (progress bar), not just a completion event.
6. **All user-facing text is in user language.** Code, files, and endpoints
   keep the internal word "isochrone"; the UI never shows it.

## Working method

**Red/green TDD, always.** Every phase lands test-first: write the failing
test, watch it fail (red), then implement until it passes (green). Never
implement before the red test exists. The repo rule is test-first — this plan
is deliberately explicit because generation/status code is easy to "verify"
by eyeballing a running server, which is not verification.

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
| POI list (label, address, modes, trips/week) | editable, add/remove | read-only |
| Commute modes per POI (train / car / walk — any combination) | editable | read-only |
| "Included in map area" badge per POI | — | — |

Rules:

- **Never trust the UI for ownership.** The server rejects edits to another
  person's record (`PATCH` scoped to the session person), even if the UI hid the
  controls.
- **Commute modes are a set, not a single pick.** A user is often happy to take
  the train OR drive, whichever is more convenient. Per POI, the user selects
  every acceptable mode (train / car / walk; car only offered when the person
  has a car). Semantics — **the default is to calculate and draw ALL chosen
  modes; walk is the only exception**:
  - **Train** chosen → a train area is drawn. Always — even when car is also
    chosen: the train area is NOT a subset of the car area (trains reach
    central London where driving is congestion-zone-impossible, and can be
    faster), so both are drawn.
  - **Car** chosen → a car area is drawn.
  - **Walk** chosen → a walk area is drawn **only when car is NOT chosen**.
    Walk ⊆ car (anything walkable is trivially drivable), so the car area
    makes the walk area redundant. But walk ⊄ train — a property can be
    walkable with no acceptable public transport — so `{walk, train}` without
    car DOES draw the walk area. A walk area is a small disk around the POI
    (person's max walk time); it participates in the map and the intersection
    like any other area.
  - A POI with no acceptable mode constrains nothing at region level.
  - School POIs (empty address) stay per-property: no address → no drawn
    area, whatever the modes.
- **The per-property gate must agree with the map area — and filter the list.**
  `acceptable_modes` restricts the app's per-property commute selector (a POI
  whose modes are `["train"]` must never be scored by a car route; a
  train+car POI is scored by the better mode, matching "whichever is more
  convenient"). The gate's per-POI durations feed the per-person **worst
  commute**, which the property list auto-filters on (see Main-page commute
  filter). Region, gate, and list share one source of truth — otherwise a
  house inside "where we could live" could be rejected by the gate or shown
  with the wrong commute.
- **Kids are a special case.** A child has no login, so `editable_by` on the
  `Person` names the adults who may edit them (superuser always allowed). Default
  for a child: all adults. George's school POIs have empty addresses → no drawn
  area (per-property only); this is the default child state, shown as "goes to
  school near the house".
- **A child CAN get a map area**: adding a real POI (e.g. "Other parent",
  OX postcode, car mode) includes it in the car shed and the intersection.
  The UI must surface the consequence: "adding this commute shrinks the
  all-commutes area" (see Intersection semantics).

### Isochrones panel (on the settings page, below the people)

Per-stage status cards with a shared state machine:

| State | Meaning | UI |
|---|---|---|
| `missing` | never generated | "Generate" button |
| `stale` | settings changed since last run (signature mismatch) | "Regenerate", amber badge "out of date" |
| `generating` | batch running | **progress bar** (see below), elapsed time, "runs in the background — you can leave" |
| `ready` | current | green, "last updated <date>", "View map" enabled |
| `failed` | stage errored | red, log tail, "Try again" (stages are resumable) |
| `interrupted` | server or machine restarted mid-batch | **auto-resumes on next server start** (no user action) |

**Progress bar (required, not a completion ping).** Each stage reports
`{done, total, message}` into the status file as it works (see Progress
model). The page shows an overall bar plus per-stage bars; a stage whose total
is unknown shows an indeterminate bar. The WebSocket pushes progress deltas
(see Progress model), so the bar moves without the user polling.

Affordances:

- **Generate / Regenerate** → confirmation dialog stating the real cost in
  plain words:
  - First run ≈ **50 min** (routes ~2,600 train stations via TfL — free API,
    no quota, one-off; ~12 OpenRouteService calls per car-commute
    regeneration — free tier: **500 calls/day, non-commercial**).
  - Re-runs are fast: only out-of-date parts run (config-signature diff),
    everything else is served from the committed artifacts.
  - The job runs in the background; the page updates live (progress bar +
    completion via WebSocket).
- **View map** → opens `/commute/commute_map.html` (new tab): one layer per
  commute (train, each car destination) + the "Where we could live" layer.
- Per-commute deep link (stretch): `commute_map.html?layer=Dad` preselects one
  layer — the map generator accepts the query param.
- **Generate is superuser-only.** The rest of the family can view status and the
  map. Rationale: generation burns free-API allocation and takes the machine.

### Main-page commute filter

The acceptable-modes setting does real work here — it **filters the houses**.

Definitions:

- **Per-person worst commute** of a house = max duration over that person's
  POIs, each computed with its acceptable modes (best acceptable mode per POI;
  a person with no POIs constrains nothing). Derived client-side from the
  existing `commutes` summaries — no new list payload field.
- **Ceiling** = the person's `fine_max_minutes` (the existing "acceptable"
  band, now documented as the hard max; the isochrone region thresholds stay
  separate and looser).

Always-on auto-filter (no user action):

- A house is hidden by default if ANY person's worst commute exceeds their
  ceiling — "pointless if anyone's doesn't work".
- Never hide silently: the list shows "N houses hidden by commute ceilings"
  with a peek toggle (off by default). Silent filtering would erode trust
  (personas: everyone is suspicious of calculations).

Narrowing control on the main page:

- **"Worst commute under X minutes"** — slider/number input whose range goes
  from the **loosest family ceiling** (the worst commute in anyone's settings)
  down to a short value (~10 min). Default position = loosest ceiling (no
  tightening beyond the auto-filter); dragging down filters the household's
  worst commute < X.
- The control is client-side over the summaries: instant, no round-trip.

### Sort by weekly commute time

The settings already know how often each commute actually happens — the same
`trips_per_week` that prices the cost (`daily_amount × trips_per_week ×
weeks_per_year`). Use it to make commute load a sort key:

- **Weekly commute minutes** per person = Σ over their POIs of
  `trips_per_week × 2 × one-way duration` (best acceptable mode per POI, from
  the same gate durations). **Household weekly time** = Σ over persons.
- `trips_per_week` is round trips (matches the cost model); a 0-trip POI (e.g.
  Simon/Dad) contributes 0 — it is a constraint, not a load. George's school
  POIs (5 trips/week, walk duration) contribute for real — the school run is
  genuine weekly time.
- **Failed or missing durations are never counted as zero.** A house whose
  commutes haven't computed shows "—" and sorts LAST (treat as +∞); a house
  with a failed commute cannot masquerade as commute-free.

UX:

- **Sort option "Weekly commute (least first)"** in the existing sort control
  (which already has a weaker `commute` sort by best single commute — the new
  household-total sort is the meaningful one; the old option stays until it's
  clearly redundant). Ascending.
- Cards show the household total in user language: "**8h 30m/week commuting**",
  with a breakdown tooltip per person ("Simon 3h 40m · Lorena 2h 15m · George
  1h 25m") — provenance matters (personas).
- Client-side over the summaries + settings (`fetchSettings` already exists);
  derivations live in `formatters/commute.ts` with unit tests.

### User language — what the UI says vs what the code says

**The word "isochrone" never appears in user-facing text** (UI copy, map
labels, dialogs, tooltips). Code, files, endpoints, and docs keep it — it is
precise internally. Mapping:

| Internal | User-facing |
|---|---|
| isochrone / transit shed | how far each commute reaches / train area |
| intersection (all-commutes) | where we could live |
| acceptable modes | how they get there (train / car / walk) |
| walk area | walking distance (drawn only when they don't drive) |
| walk-only POI | within walking distance (no drawn area only when they also drive) |
| generated_at / stale | last updated / out of date |
| generate / regenerate | update the map / update again |
| stage names (transit-shed, drive, …) | never shown |

Map layer labels (shipped with this plan): one layer per POI per drawn mode —
"Train: Pimlico & Aldgate", "Drive to Dad", "Drive to Bracknell",
"Where we could live". A POI with several modes gets one layer per mode
(e.g. "Train to Pimlico" and "Drive to Pimlico" both drawn).

## Architecture

### The toolchain stays out-of-process

Never port the batch into the app. It is a 50-minute, thousands-of-calls,
quota-consuming, committed-artifact pipeline; the DAG scheduler is for
per-property refreshes. The app **orchestrates**:

```
POST /api/isochrones/generate
  └─ houses/isochrones.py (new)
       ├─ derive config from settings → write toolchain inputs
       ├─ spawn tools/commute/run.py DETACHED (own session, start_new_session)
       └─ run.py owns the batch: stage order, config-signature skip, writes
          data/commute/generation.json (state, pid, per-stage progress)
Server startup (lifespan): if generation.json says running/interrupted and the
recorded pid is dead → respawn run.py (auto-resume; checkpoints skip done work)
```

Rules:

- **One generation at a time.** Concurrent `POST /generate` → 409 while the
  recorded pid is alive.
- **Never kill a running stage.** The detached child finishes even if the
  server dies; the next server start reads its result.
- **Serve, don't gate.** `app.mount("/commute", StaticFiles(directory="data/commute"))`
  serves the map and payloads read-only. Atomic writes make mid-generation reads
  safe (readers get the old or new file, never a partial). `make commute-serve`
  (port 8123) stays for phone viewing without the app running.

### Progress model

`tools/commute/run.py` (new runner module) owns the batch and the status
file; the app only spawns and reads it. Each stage writes progress into
`generation.json`:

```json
{"name": "transit-shed", "state": "running",
 "progress": {"done": 1240, "total": 2600, "message": "Checking train stations"}}
```

Toolchain progress sources (Phase 2 work):

| Stage | Progress source |
|---|---|
| transit-shed | existing per-25-station checkpoint → `done`/`total` stations |
| drive | per matrix request → `done`/`total` requests per destination |
| intersection / map | near-instant; indeterminate bar |

The app runs a lightweight poller while a generation is in flight (reads
`generation.json` every ~3 s) and broadcasts deltas over the existing
WebSocket; the settings page renders the bars from those events.

### Crash survival

Never rely on a human clicking "resume":

1. The runner is spawned **detached** (own session) — a server crash does not
   kill it.
2. The runner records `{state, pid, started_at}` in `generation.json` and
   writes its own stage/progress updates, so it stays observable with no
   parent.
3. On server startup (FastAPI lifespan), read `generation.json`: if a
   generation is `running`/`interrupted` and the recorded pid is **not alive**
   (server or machine died, or the child was killed), **auto-respawn the
   runner** — stages whose config signature matches are skipped; partially
   completed stages resume from their checkpoints (atomic writes make this
   safe).
4. The runner handles `SIGTERM`/`SIGHUP` by marking itself `interrupted` and
   exiting cleanly (checkpoint resume covers the rest).

### Status model

`data/commute/generation.json` (runtime, gitignored):

```json
{
  "state": "generating",           // running | ready | failed | interrupted
  "pid": 40123,
  "started_at": "...", "finished_at": null,
  "settings_signature": "sha256",  // persons+POIs+thresholds hash
  "stages": [
    {"name": "transit-shed", "state": "ready", "exit_code": 0, "generated_at": "...",
     "progress": {"done": 2600, "total": 2600, "message": "Checking train stations"}},
    {"name": "drive",        "state": "generating", "progress": {"done": 4, "total": 6, "message": ""}},
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
| Transit offices = `simon_destination`/`lorena_destination` in `houses/config.py` | POIs whose modes include train (drawn regardless of car) |
| `data/commute/drive_destinations.json` (hand-maintained) | **generated** from POIs whose modes include car |
| No walk areas | POIs whose modes include walk AND not car → small disk around the POI (max walk time) |
| Thresholds 132/90 min | new `isochrone` settings block: `{transit_min: 132, drive_min: 90}`, per-POI override |
| Kids excluded implicitly | school POIs (empty address, walk) → per-property only; a kid's real POI participates |

Data model changes:

- `PlaceOfInterest.acceptable_modes: list[str]` (`"train" | "car" | "walk"`,
  any combination) — replaces the single `isochrone_mode` idea. `None`/empty on
  read means "unset": migrated by rule (Pimlico/Aldgate → `["train"]`,
  Bracknell/Dad → `["car"]`, school → `["walk"]`), explicit thereafter. The
  toolchain's shed inputs derive from it: **train destinations = POIs with
  train (drawn even when car is also chosen); car destinations = POIs with
  car; walk areas = POIs with walk and no car** (address-bearing POIs only —
  school POIs have no address and stay per-property). **Never infer
  acceptability at generation time** — the app's per-property selector
  (walk→transit→drive) is about picking a route, not about which modes the
  person will accept.
- `Person.is_child` exists; add `Person.editable_by: list[str]` (default `[self]`,
  superuser implied; child default = all adults).
- `stations.csv`/TfL destinations: `station_shed.py` gains a `--config` input
  (destinations + threshold) instead of reading `houses/config.py` — the app's
  derived config is the new source.

### Intersection semantics ("where we could live")

Per POI, the constraint area is the **union of its drawn mode areas** (any
acceptable mode works); "where we could live" is the **intersection across
POIs** (everyone's commutes must work):

```
where_we_could_live = ⋂ over POIs p of ( ⋃ over drawn modes m of area(p, m) )
```

Known approximation: the transit shed is one region for all train destinations
(a station within threshold of ANY train destination is kept), i.e. an OR
inside the train family — inherited over-coverage, absorbed by the per-property
gate. Phase 3 refinement: per-POI train areas (station_shed.json already stores
per-destination durations) intersected exactly.

### API surface

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /api/settings` | any | unchanged; persons now carry `editable_by_me`/`acceptable_modes` |
| `PATCH /api/settings/person/{name}` | **own person / superuser / in `editable_by`** | replace `PUT /settings/persons` (removes the whole-list, no-authz endpoint) |
| `GET /api/isochrones/status` | any authenticated | per-stage state + staleness + links |
| `POST /api/isochrones/generate` | superuser | start/resume the batch; 409 while running |
| `GET /commute/*` | any (app behind auth) | static artifacts (map HTML, payloads) |
| WebSocket `isochrone-status` event | session | live updates via existing `/api/ws` + `useWebSocket` |

## Phases

### Phase 1 — settings page + authorization

1. `Person.editable_by`, `PlaceOfInterest.acceptable_modes` (train/car/walk
   set) with migration; `PATCH /api/settings/person/{name}` with server-side
   ownership; remove `PUT /api/settings/persons`; the per-property commute
   selector honours `acceptable_modes` (train-only POIs never scored by a car
   route). (Red/green: authz matrix + selector-restriction tests first.)
2. `SettingsView.vue` + `/settings` route + header link: family sections,
   own-vs-other rendering, child styling + school note, POI editor with mode
   checkboxes. (Red/green: component tests for own/other/child rendering first.)
3. **User-language sweep**: every new UI string checked against the naming
   table; map layer labels renamed to "Train: …"/"Drive to …". (Red/green:
   a test asserting no UI string contains "isochrone".)
4. **Worst-commute auto-filter + "worst commute < X" control on the property
   list**: per-person worst commute vs `fine_max_minutes` ceiling, hidden
   count + peek, slider from loosest ceiling down. (Red/green: filter
   derivation tests + list component tests first.)
5. **Sort by weekly commute time**: `weeklyCommuteMinutes` derivation in
   `formatters/commute.ts` (trips/week × 2 × duration, 0-trip POIs, failed
   commutes sort last), sort option + card display with per-person breakdown
   tooltip. (Red/green: formatter tests + sort component tests first.)

### Phase 2 — generation + map

4. `tools/commute/run.py` (runner: stage order, progress, pid, SIGTERM handling);
   stage progress hooks in `station_shed.py` and `drive_isochrone.py`;
   `station_shed.py` `--config`. (Red/green: runner tests with a fake stage
   script — progress fields, interrupted-on-signal, resume-skips-done-stages.)
5. `houses/isochrones.py`: config derivation, detached spawn, **startup
   auto-respawn** (lifespan hook), WebSocket progress poller. (Red/green:
   crash-resume test — spawn, kill the child, boot the app, assert it respawns
   and completes; progress broadcast test.)
6. Endpoints `GET /status`, `POST /generate` (409, superuser), static mount.
7. Isochrones panel UI: state machine, cost dialog, **progress bars**, view-map
   button. (Red/green: component tests with fake status events.)
8. Tests: derivation (settings→destinations+thresholds), status transitions
   (fake `tools.commute` subprocess), 409 concurrency, e2e render.

### Phase 3 — polish

8. `?layer=` deep links; per-POI "included in map" badges; empty-intersection
   callout ("no house satisfies every commute — relax a threshold or a POI").

## Decisions & risks

| Decision | Rationale |
|---|---|
| Batch stays a subprocess, not a DAG node | 50-min job, thousands of one-off API calls, committed-artifact pipeline; the DAG scheduler is per-property refresh |
| Batch auto-restarts on server crash | Detached runner owns state; the app's startup hook respawns it when the recorded pid is dead |
| UI updates live with a progress bar | Runner writes per-stage `{done, total}`; app polls and broadcasts via the existing WebSocket |
| Whole-family page, read-only others | Buying together → everyone's commutes are everyone's context; ownership is per-field, enforced server-side |
| Generate = superuser-only | Quota burn + machine load; rest of family reads status and the map |
| Strict intersection (AND of every POI's constraint area) | "Where we could live" must satisfy everyone; UI shows which POIs feed it so a shrinking area is explainable; empty state handled explicitly |
| Acceptable modes are a set (train/car/walk); ALL chosen modes are drawn; walk is the only exception | Walk ⊆ car (redundant when driving), but walk ⊄ train (a property can be walkable with no usable public transport) — so the walk area is drawn only when car is not chosen; train is always drawn when chosen, even alongside car |
| `acceptable_modes` explicit, never inferred | The app's per-property selector is about route choice, not acceptability |
| Always-on commute ceiling filter + optional tightening slider | Settings ceilings are the hard floor ("pointless if anyone's doesn't work"); the "worst commute < X" slider only tightens from the loosest family ceiling; hidden count + peek keeps filtering visible (trust) |
| Weekly commute sort uses settings trips/week × round-trip duration | Same data that prices the cost; 0-trip POIs contribute 0; failed/uncomputed commutes sort last, never as zero (a broken house cannot look commute-free) |
| Runtime state in `generation.json`, artifacts stay committed | Reproducibility/reviewability of artifacts unchanged; runtime state is ephemeral |
| **Risk: 50-min first run vs uvicorn `--reload`** | Detached runner + startup auto-respawn; the toolchain's checkpoints make resume safe |
| **Risk: ORS free tier (500/day, non-commercial)** | Batch uses ~12 calls; regeneration only on settings change; warn in the dialog |

## Acceptance

1. `PATCH /api/settings/person/{name}` for another person → 403; own → 200;
   guardian edits child → 200. `PUT /api/settings/persons` gone.
2. Editing a POI mode/address/threshold flips the isochrone panel to `stale`.
3. `POST /api/isochrones/generate` (superuser) runs only stale stages, writes
   committed artifacts + `generation.json`, pushes progress + completion
   WebSocket events; concurrent POST → 409; non-superuser → 403.
4. **Crash survival**: kill the running child mid-batch, restart the app → the
   batch auto-respawns and completes without any user action.
5. **Progress**: while generating, the page shows per-stage bars driven by
   WebSocket deltas, not a completion ping.
6. **User language**: no UI string (settings page, dialogs, map labels)
   contains "isochrone", "transit", or "shed" (enforced by a test).
7. **Commute filter**: houses whose per-person worst commute exceeds the
   person's `fine_max_minutes` ceiling are hidden by default (with a visible
   hidden count + peek); the main page's "worst commute < X" control narrows
   from the loosest family ceiling down to ~10 min.
8. **Weekly commute sort**: "Weekly commute (least first)" sorts by household
   Σ trips_per_week × round-trip duration; 0-trip POIs contribute 0; houses
   with uncomputed commutes sort last; cards show the total + per-person
   breakdown.
9. `GET /commute/commute_map.html` renders the map with per-commute layers and
   the "Where we could live" layer.
10. `make test` green; red/green TDD throughout (each phase lands with its red
   tests written first, then green).
