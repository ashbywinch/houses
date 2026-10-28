# Implementation Plan — Accessibility Screening & Retrofit Costing

**Audience:** engineers/agents implementing the feature; the PRD owner (plain-language
gates).
**PRD:** [prd-accessibility-screening.md](prd-accessibility-screening.md).
**Hardware constraint:** development runs on an 8GB laptop — the floor-plan pipeline must
be **local and CPU-only** (per the CubiCasa5K / lightweight-segmentation decision: a
ResNet-34 UNet, 4-class floor/wall/door/window, ~98 MB weights, val mIoU 0.983 —
comfortably inside 8GB RAM).

**Cost rules (revised 2026-08-04):**
- **AI is allowed in every stage.** Cost is managed by *ordering*, not prohibition:
  within each release the cheapest evidence runs first — metadata rules → local
  geometry → tiny local models → paid AI (photos, survivors only) → free satellite
  tiles + local segmentation. Cheap checks front-load the work; paid AI only ever runs
  on houses that survived the cheaper gates.
- **No new paid APIs or software.** Data sources must be free (OGL/open) or already in
  use by this repo; the AI service is the one paid thing.
- **The spreadsheet MUST NOT be touched.** No new columns, no writes, no formula sync.
  The app is the only delivery surface — enforced by `test_no_sheet_writes.py` in
  `make test`, not just stated.

## 1. Measurement semantics — what "measured" means

The only authoritative measurements on a floor plan are the **dimension texts the estate
agent wrote** (laser, wall-to-wall). The system never re-measures a room and claims
accuracy against an unknown "truth". Four evidence classes, labelled on every claim:

| Label | Meaning | Source |
|---|---|---|
| `as_stated_on_plan` | Quoted from the plan's dimension text, **verbatim** ("4.2m × 3.6m"), attached to the room whose walls span it | plan annotation reading |
| `estimated_from_plan` | Computed from plan geometry (scale from annotations × pixels): door positions, hallway clear widths, turning circles, unannotated spans | plan geometry |
| `inferred_from_photo` | Judged from listing photos: door clear widths, patio-door vs window, thresholds, step counts, floor covering, external route | photo inference |
| `inferred_from_satellite` | Judged from satellite imagery post-address-confirmation: plot outline, driveway, garden, passages, outbuildings | satellite analysis |

Rules: `as_stated_on_plan` values are shown exactly as written — never "corrected".
All other classes are always shown with their label, never as fact. **Doors are a photo
problem**: the plan shows where an opening is, but agents do not annotate door widths,
so clear width comes from photos (banded, see R2); plan geometry only supplies position.

## 2. Context — what already exists

| Asset | Where | Use here |
|---|---|---|
| Rightmove ingestion | Firefox Page Assist → `POST /inject-property` (`houses/server.py`) | Screening hooks in here; **payload must be extended with floor-plan + photo images, `floor_level`, `has_lift`** (today it is structured text) |
| Enrichment module pattern | `docs/adding-a-new-enrichment-module.md` | Module → protocol in `services.py` → default impl → `_run_enrichment` → `Row.HEADERS` → fake in `tests/helpers.py` |
| DAG library | `dag/`, `docs/dag-library.md` | Nodes: `compute() -> Attempt`, typed values, stable deps, `node_id` bumping, provenance via `provenance_formula` (see `houses/nodes/total_works_node.py`) |
| Works-cost precedent | `houses/nodes/total_works_node.py` | Template for the conversion-cost node (sum + gate on missing inputs) |
| Settings nodes | `houses/nodes/settings_node.py` (`SETTING_DEFAULTS`) | New settings: `accessibility_budget` (£200k), `accessibility_dimension_profile` |
| Sheet schema | `houses/sheets/row.py` (`Row.HEADERS`, `from_property`) | **Must NOT be touched — app-only delivery (§8)** |
| OpenRouter LLM use | `houses/town_desc.py` (existing vision/text provider) | Photo-inference micro-agents (R2) and rework proposals (R3) reuse the same provider + key |
| Feature-doc template | `docs/rightmove-commute-monitor.md` | Spike-first, stages, Makefile targets, committed data artifacts |
| Persona | `docs/personas.md` (Ashby — disabled, needs annexe) | Requirements source; feature serves an existing persona |

## 3. Architecture

```
metadata + plan image + photos (extended inject payload)
  │
  ▼
[Stage 1] Dealbreaker screen — houses/accessibility/screen.py   ← cheapest checks first
  ├─ D1 entrance (metadata: flat/apartment without lift; step-count photo classifier — tiny local model)
  ├─ D2 bathroom (plan geometry)   ├─ D3 outdoor space (plan sketch + photos)
  ├─ D5 bathroom privacy (plan geometry)
  ├─ D4 bedroom noise (garden evidence — only when determinable, else "needs checking")
  └─ fail-fast: first definite fail exits; survivors proceed
  │
  ▼
[Stage 2] FloorPlanAnalysis — houses/floorplan/ (local, CPU)
  ├─ read.py      dimension text read VERBATIM + assigned to rooms/wall-pairs  → as_stated_on_plan
  ├─ segment.py   UNet (CubiCasa5K) → wall/door/window masks                  → estimated_from_plan
  ├─ geometry.py  scale S (from annotations), door positions, hallway widths, turning circles
  └─ graph.py     rooms + doorways + adjacency; room naming; garden-boundary sketch
  │
  ▼
[Stage 3] Image inference — houses/photo_infer/ + houses/satellite/    (survivors only; paid AI service, cost per house measured)
  ├─ R2: door widths, patio doors, thresholds, step counts, floor covering (photos)
  ├─ R4: outdoor polygons, passage width, driveway (satellite, post-address-confirmation)
  └─ Q19 external route (photos R2; satellite refinement R4) — can rescue D1
  │
  ▼
[Stage 4] Reasoning & plans — houses/accessibility/ + houses/nodes/accessibility.py
  ├─ R1: scorecard (plan-derived) + cost + affordability verdict (the £200k filter)
  ├─ R3: rework proposals (Tier-1 rules + Tier-2 LLM + verifier) → potential plans
  │      (ramps drawn + snagged, soil routes assumed) → revised floor plan
  └─ R4: PlanningAssessment (permission classes, heritage lookup, parking) (Q18)
  │
  ▼
[Stage 5] API + frontend — score badge, sort by score, scorecard detail, evidence
           (R1 ships the MVP UI slice; R2–R4 grow it — never a final phase)
```

Failure semantics per `dag-library.md`: services return `Attempt[...]`; a floor plan that
cannot be read returns `Attempt.impossible("floor plan unreadable")` → **"needs
checking"**, never a verdict. **Never hide the reason; never auto-reject on a guess.**
Missing photos degrade evidence quality (items become `unknown`), never cause a
rejection.

## 4. Eval conventions — borrowed from adjacent projects

| Pattern | Source | Adopted rule here |
|---|---|---|
| `evals/` gated by `RUN_EVALS=1`; never in CI; deliberate spend | books_to_anki (`evals/conftest.py`) | `tests/evals/conftest.py` skips everything unless `RUN_EVALS=1` |
| Eval = named regression case from a real failure, asserting a contract | books_to_anki | Each eval names the trap it encodes (e.g. "front-door-facing loo", "carpet bedroom") |
| "If it doesn't call a real API/model, it's a test, not an eval" | chat-workflow (its own testing doc — not this repo) | Parser/photo evals run the **real pipeline** on real fixtures; pure-logic checks stay in `tests/unit` |
| Live evals **fail (not skip)** when keys/artifacts missing | chat-workflow | Photo-inference evals fail if no API key; model evals fail if `make floorplan-model` wasn't run |
| `make evals` + affected-evals selection | chat-workflow Makefile | `make evals`; `make evals-incremental` via the code-review-graph dependency map |
| Golden labelled sets committed under `tests/fixtures/` | feed-generator | `tests/fixtures/floorplans/` + `labels.json` (+ photo + aerial labels), committed `-diff`/`linguist-generated` |
| Eval thresholds surfaced as config; drift test | feed-generator | Thresholds in `houses/accessibility/eval_thresholds.py`; a unit test asserts the pipeline and evals read the **same** constants |

**Golden corpus ownership:** every label is either (a) hand-verified by Ashby, or
(b) synthetic (SVG → raster, or a photo/aerial of a known site). Origin recorded per
entry. No machine-generated labels — evals must not validate themselves.

**Two hard rules:**
1. **Never auto-reject a house we know is passable** ("zero false fails") — a wrong
   rejection destroys trust; a miss costs one click and is routed to "needs checking".
2. **Every question in the registry (§5) ships only with its own labelled eval suite.**
   A question without evals doesn't ship. `test_registry_coverage.py` (runs in
   `make test`) fails CI until every registry question is covered by at least one eval
   file — the registry and the evals cannot drift apart.

## 5. The question registry — every personal question, its verdict, its eval

This is the authoritative checklist. It is derived **from the interview** — each row is a
question the user actually asked, in their words — plus everything added since (doors,
thresholds, steps, flooring, planning, external route). Process rule: **a new personal
question enters the registry first** (with a class and an eval), *then* it can be
implemented. The PRD's dealbreakers and scored goals are the product-facing view of this
table; this table is the testable one.

| # | Question (as asked) | Kind | Answer comes from | Eval file | Gate (plain English) |
|---|---|---|---|---|---|
| Q1 | Can we create level access? (the 20-step house) | Dealbreaker D1 | metadata (flat/apartment, lift) + entrance-photo step classifier (local); **rescued by an evidenced alternative level route** (side/rear entrance or front-to-back outside route with rear level access, see Q19) | `test_entrance_evals.py` | ≥ 19 in 20 step houses caught; **zero false fails** |
| Q2 | Can there be a downstairs bathroom? | Dealbreaker D2 | plan: bathroom present, or a room ≥ ~2.2×1.5 m against an outside wall to add one | `test_bathroom_evals.py` | ≥ 9 in 10 |
| Q3 | Is there semi-private outdoor space — not a car park or bin alley? | Dealbreaker D3 | **screen (R1):** plan garden sketch (presence) + photos; **refined (R4):** garden extent/quality from satellite | `test_outdoor_evals.py` | ≥ 9 in 10 |
| Q4 | Is the bedroom away from the family outdoor seating (noise at night)? | Dealbreaker D4 | garden-use evidence (photos/map) — **only when determinable**; else needs-check | `test_bedroom_evals.py` | rejects only with evidence; 100% of its rejects correct |
| Q5 | Can the downstairs bathroom be private — ideally en suite? | Dealbreaker (privacy) + Scored (en suite) | plan: WC sightline from front door + alternative locations; en-suite = door from bedroom | `test_bathroom_evals.py` | privacy fails ≥ 9 in 10; en-suite verdicts ≥ 9 in 10 |
| Q6 | Is there a downstairs bedroom, or can we make one? | Scored (Sleeping) | plan: bedroom present, else convertible room (≥ ~9 m², window, near bathroom); **view quality (photos, R2)** | `test_bedroom_evals.py` | ≥ 9 in 10 present/convertible verdicts |
| Q7 | Can it be a level-access wet room? | Scored (Bathing) | plan (room size, drainage-adjacent wall, plumbing route) + photos (existing bath/shower) | `test_wetroom_evals.py` | ≥ 9 in 10 |
| Q8 | Is there a kid/dog-excludable sitting space with a view? | Scored (Sanctuary) | plan (room adjacent to the suite, closable door) + photos (view) | `test_sanctuary_evals.py` | ≥ 8 in 10; unknowns allowed, never guessed |
| Q9 | Annexe, main-house suite, or both? | Scored (configuration mode) | plan: garage conversion potential (contiguous, own access) + main-suite viability; **outbuildings only post-address-confirmation (satellite, R4)** | `test_config_evals.py` | ≥ 9 in 10 modes correct |
| Q10 | Can the power chair get from the front door to its storage — charging included — without blocking? | Scored (Storage/route) | plan graph + estimates (route continuity, chair-parking spot, socket) | `test_route_evals.py` | ≥ 9 in 10 |
| Q11 | Are hallways and turning spaces wide enough? | Scored (Dimensions) | plan estimates (1050/1200 mm hallways, 1500 mm turning, profile-dependent) | `test_annotation_geometry_evals.py` | within 10 cm of a careful human on the same plan; verdicts ≥ 9 in 10 |
| Q12 | Are the doors on my route wide enough? | Scored (Dimensions/Entering) | **photos (R2)** — width bands (<700 / 700–780 / 780–850 / >850 mm) | `test_door_width_evals.py` | ≥ 9 in 10 doors in the correct band |
| Q13 | Which openings are patio doors, and do they give level access? | Scored (Outdoor) | photos (R2): patio vs window ID; threshold profile | `test_outdoor_evals.py` | ≥ 9 in 10 openings correct |
| Q14 | How many steps to each exterior door? | Scored (Entering/Outdoor) | photos (R2) per exterior door (entrance, patio, side/garage) | `test_outdoor_evals.py` | exact counts ≥ 9 in 10 |
| Q15 | What's the floor covering in each room? | Scored (Flooring) + cost input | photos (R2) per room (carpet / hard / unknown) | `test_floor_covering_evals.py` | ≥ 9 in 10 rooms correct |
| Q16 | Can we reach the kitchen and washing machine on the level? | Scored (Main-house access) | plan graph: level route from suite to kitchen/laundry — **shortens daily travel, the energy budget** | `test_route_evals.py` | ≥ 9 in 10 |
| Q17 | What does it cost — does it fit £200k? | Cost + affordability (user filter) | cost table × required fixes; `price + total ≤ budget` | `test_affordability_evals.py` | total within 15% of hand pricing; 199/200/201k boundary |
| Q18 | Does the work need planning or building control — how likely is approval? | Scored (Planning & approvals, R4) — **a strike, never a dealbreaker** | fix list → permission class; **heritage lookup** (planning.data.gov.uk); garage conversion → **retained parking (satellite, post-confirmation)**; approval-likelihood band | `test_planning_evals.py` | permission class ≥ 9 in 10; parking ≥ 9 in 10; likelihood bands ≥ 9 in 10 |
| Q19 | Is there an outside route front-to-back, wide/level enough — **with level access feasible at the back**? | Scored (Entering/route, R2) — **and D1 rescue** | photos (passage, width bands, gate, surface, **rear door + rear threshold**) + **satellite passage width (R4)**; rescue: front blocked + passage + rear level evidenced → pass via side; partial evidence → needs-check | `test_external_route_evals.py` | passage detection ≥ 9 in 10; width bands ≥ 9 in 10; rear-access ≥ 9 in 10; rescue path ≥ 9 in 10 |

Cross-cutting technical issues (annotation reading, door position, turning-circle
geometry) have their own component evals in the same files — the registry rows above are
the *questions* those components exist to answer.

### 5.1 Dealbreaker vs scored factor vs user filter — explicit

One rule decides everything: **a dealbreaker is a barrier no amount of money removes.**
Anything fixable with money, or a quality/risk that varies, is scored. The user's own
budget cap is their filter, applied after scoring — never an automatic fail.

| Class | Definition | Applies to |
|---|---|---|
| **Dealbreaker** | Cannot be fixed with money — structural, site, or layout impossibility. Auto-removed by the screen, reason shown | Q1 (level access), Q2 (downstairs bathroom), Q3 (semi-private outdoor space), Q4 (bedroom noise, evidence-gated), Q5-privacy (bathroom privacy with no alternative) |
| **Scored factor** | Fixable with money, or a quality/risk that varies. Contributes to the score; trade-offs between factors are legitimate (the exception view-bedroom house) | Q5-en-suite, Q6, Q7, Q8, Q9, Q10, Q11, Q12, Q13, Q14, Q15, Q16, Q18, Q19 (Q19 also feeds D1's rescue) |
| **User filter** | The user's own preference knob — sortable/filterable, configurable, never an automatic fail | Q17 (affordability: the £200k cap) |

Standing rules:
1. **Q17 (cost) is a filter, not a dealbreaker** — per the user's own rule ("nothing is a
   dealbreaker if I can fix it with money"). The cap is a setting, default £200k.
2. **Q18 (planning/building control) is a strike, never a dealbreaker** — requiring
   permission, or a low approval likelihood, lowers the score and is flagged; it never
   auto-rejects.
3. **A question can be split across classes** — Q5 is dealbreaker (privacy) *and* scored
   (en-suite); Q14's entrance step count feeds Q1, but Q14 itself is scored.
4. When a new question is added to the registry, it must be classified in one of these
   three classes before implementation — no unclassified questions.

### 5.2 Q18 evidence — how the lookups work

Verified sources (England; the search area is England — Scotland/Wales/NI deferred):

| Concern | Source | How | Fallback when it fails |
|---|---|---|---|
| Listed building | **planning.data.gov.uk** (DLUHC Planning Data, OGL v3.0, free, no key) | one point query per property: `GET /entity.json?latitude=LAT&longitude=LON&dataset=listed-building&dataset=conservation-area&limit=100` — both flags in one call; entity `reference` → Historic England list-entry URL for evidence | no hit → `None` (needs checking), **never assumed safe** — the API's own docs warn coverage varies by LPA |
| Conservation area | same call as above | point-in-polygon against the ~11k conservation-area polygons (authoritative-with-gaps, OGL) | no hit → `None` unless Stage 0 verified full LPA coverage → `false` with `coverage: verified_good` label |
| Grade / evidence | Historic England NHLE (official register, OGL, daily updates) | **no second lookup** — NHLE list-entry URL built from the planning.data.gov.uk entity reference | URL built only when the reference exists |
| Retained off-street parking | **satellite, post-address-confirmation (R4)**: driveway footprint; plan supplies the garage room | rule: garage converted → ≥ 1 off-street space must remain | **pre-confirmation** → `garage_parking_ok: None` (needs-check); satellite unclear → `None` |

Operational rules:
- Lookup runs **only for survivors** (R4), once per property, **disk-cached** in
  `data/api_cache/` (repo pattern) so re-analysis is offline and free.
- Property coordinates come from the existing geocode (`houses/location.py`).
- Every hit records its evidence URL; every miss records
  `coverage: verified_good | patchy | unknown` so the scorecard can say *"not listed —
  checked against [source]"* or *"couldn't verify — needs checking"*.
- **Stage 0 spike** verifies coverage on ~20 sample coordinates; go/no-go recorded in
  writing (if patchy, the Q18 gate lowers to "≥ 9 in 10 where coverage is verified,
  rest needs-checked").

### 5.3 Dependency audit — what needs what, and when

#### Pipeline dependency chain (data flow)

| Stage | Needs | Produces | Consumed by |
|---|---|---|---|
| Payload (scraper) | — | `floor_plan_urls`, `photo_urls`, `floor_level`, `has_lift` | every stage; **Stage 0/R1 prerequisite** |
| 1 Screen | metadata, `FloorPlanAnalysis` (or None), photos | `ScreenVerdict` | Stage 4 |
| 2 Parser | plan image; model weights + corpus (Stage 0) | `FloorPlanAnalysis` | Stages 1, 3, 4 |
| 3 Image inference | graph (crop selection), photos, OpenRouter; **satellite + confirmed address (R4 only)** | `PhotoInference` (R2); `OutdoorAnalysis` (R4) | Stage 4 |
| 4 Reasoning & plans | pass verdict, analysis, inferences, settings, price, cost table, planning rules + heritage spike (Stage 0) | Scorecard, `CostEstimate`, affordability (R1); `ReworkProposals` + `AsIsPlan`/`PotentialPlan`s (R3); `PlanningAssessment` (R4) | Stage 5 |
| 5 API/UI | Stage 4 outputs; `revised_plan.py` | sorted list, detail, overlay, revised plan | user |

#### Release prerequisites

| Stage | Requires (from) | Must exist before |
|---|---|---|
| 0 Spikes | existing ingestion; **`make floorplan-model` (action here)**; **eval scaffolding (action here)**; **payload extension (action here)** | every release |
| R1 MVP | Stage 0; payload | everything after |
| R2 Photos + Q19 | R1 (parser, screen) | R3–R4 (R3 does not depend on R2) |
| R3 Rework + potential plans | R1; rework spike outcome; OpenRouter | R4 (R4 does not depend on R3's LLM tier) |
| R4 Planning + satellite | R1–R3 outputs; **address-confirmation UI (built in R3)** | final acceptance |

Every release's gate is runnable with only the inputs in its own row.

#### Output → consumer → eval matrix

Every output, what consumes it and demands of it, and the eval that asserts it. An
output with an empty eval column is a hole — enforced by
`test_registry_coverage.py`-style linkage.

| Output (release) | Consumers & their requirement | Eval |
|---|---|---|
| `FloorPlanAnalysis.annotations` (R1) | Q17 cost uses room **area**; Q6/Q7/Q15 need room identity; overlay quotes verbatim | annotation reading: 19/20 verbatim + placed right |
| `.graph` room kinds (R1) | D2/Q6/Q9 need correct room naming; Q7 needs size + drainage wall | room classification ≥ 9/10 |
| `.graph` garden sketch (R1) | D3 *presence* at screen time (never dimensions) | sketch-presence eval |
| `.graph` doorways: positions (R1) | D5 sightline | door-position eval |
| `.graph` doorways: topology (R1) | Q10/Q16 route continuity | doorway-topology eval ≥ 9/10 |
| `.estimates` + scale (R1) | Q10/Q11 verdicts; screen D2/D3 | within 10 cm of human-on-plan; S ±5% |
| `ScreenVerdict` (R1) | Stage 4 gating; user trust | entrance/bathroom/outdoor/bedroom suites; zero false fails |
| `PhotoInference` doors (R2) | Q12 score; Q17 door-widening cost | door-width bands ≥ 9/10 |
| `PhotoInference` openings (R2) | Q13 | patio/window ID ≥ 9/10 |
| `PhotoInference` thresholds/steps (R2) | Q13/Q14; contradiction rule vs screen pass | ordinal + exact counts ≥ 9/10; contradiction → needs-check (e2e) |
| `PhotoInference` floor coverings (R2) | Q15; Q17 flooring cost (£/m² × area) | flooring ≥ 9/10; affordability ±15% covers the cost link |
| `OutdoorAnalysis` polygons (R4) | D3 quality, Q9 outbuildings, Q18 parking — blind without them | outdoor-polygon eval ≥ 9/10 vs labelled aerial fixtures |
| `OutdoorAnalysis` passage (R4) + photo passage (R2) | Q19 external route; D1 rescue | passage detection + width bands ≥ 9/10; rescue path ≥ 9/10 |
| `AccessibilityScorecard` (R1, refined R2–R4) | list sort, detail; Ashby review | registry-coverage test; e2e evidence links; human gates |
| `ReworkProposals` (R3) | config mode, cost drivers, revised plan — **only verified proposals** | `test_rework_evals.py`: verifier ≥ 9/10; plausibility ≥ 8/10 |
| `AsIsPlan` / `PotentialPlan`s (R3, R4 refines) | UI revised plan; config mode; cost drivers | `test_route_geometry_evals.py`: ramp footprint + snags ≥ 9/10, recompute on boundary change; `test_soil_route_evals.py`: plausibility ≥ 9/10, load-bearing downgrades ≥ 9/10 |
| `PlanningAssessment` + `HeritageLookup` (R4) | Q18 score/strikes | permission class, parking, likelihood ≥ 9/10; heritage flags ≥ 9/10 vs labelled coords |
| `CostEstimate` + affordability (R1, refined R2–R4) | Q17 filter | ±15% of hand pricing; 199/200/201k |
| Sheet (constraint) | must NOT be touched — no columns, no writes | `test_no_sheet_writes.py` (unit): no accessibility module imports `houses.sheets` or writes a row |
| List/detail API (R1 minimal, grows) | sorting, evidence links | e2e |

### 5.4 Outdoor data timing — the two passes

Outdoor polygons need the **confirmed address** (satellite imagery), so everything that
consumes them is timed by the app's address-confirmation step. Build happens in R4 with
aerial fixtures; *production output* waits for confirmation. Every claim records which
pass produced it.

| Pass | When | Outdoor evidence available | Status of outdoor-dependent items |
|---|---|---|---|
| **Pre-confirmation** | at screen + initial score | plan garden sketch (presence only) + listing photos | D3: provisional presence; Q9 outbuildings: `unknown`; Q18 parking: `None` → needs-check; Q19: photos only |
| **Post-confirmation** | user confirms address/location in the app → enrichment re-run | satellite polygons (driveway, garden, passage, outbuildings) | D3: quality; Q9: outbuilding variant; Q18: parking verdict; Q19: passage width |

Rules:
1. A pre-confirmation claim is always labelled `pre_confirmation` — never final.
2. A confirmed address is required before any outdoor-dependent *verdict* is shown as
   final; until then those stay provisional/`unknown`.
3. Q19's photo evidence can **rescue D1 at screen time** (front blocked + evidenced
   side route + rear level access → pass via side); satellite only refines afterwards.
4. The satellite source is **ESRI World Imagery (free tile service)** — no new paid
   APIs; the repo's existing Google keys are not extended for this feature. Stage 0
   spike decides, gated on segmentation accuracy — failure keeps outdoor items
   `unknown`.
5. **Boundary change → recompute (§5.6):** potential plans (ramp footprints, access
   routes) are functions of the plot outline. Any boundary update **recomputes all
   potential plans** and re-labels their claims with the new pass.

### 5.5 Rework reasoning — how "can the house be reworked?" is answered

The rework questions (Q2, Q5, Q6, Q7, Q9 plus the revised floor plan) are the hardest
reasoning in the feature. Neither pure approach works alone: LLMs hallucinate spatial
proposals, and hand-written rules have limited coverage. Design: **two tiers + a
deterministic verifier** (the PDF's hybrid paradigm and consensus loop, applied to
proposals).

**Tier 1 — Constraint rules (deterministic, always on, free).** The rule base that
produces the Q2/Q5/Q6/Q7/Q9 verdicts also emits a **constraint envelope**: per room,
which rework types are geometrically possible (`add_bathroom`, `convert_garage`,
`wet_room`, `new_partition`, `widen_door`, `ramp`), the hard constraints (min sizes,
drainage-adjacent walls, privacy sightlines, route continuity), and an explicit
**"can't know from a plan" list** — ceiling height, load-bearing walls, floor levels,
soil-stack positions, damp, party-wall condition. The can't-know list is a feature: it
becomes the `needs survey` annotations and the `needs_survey` cost exclusions.

**Tier 2 — Proposal generation (LLM, survivors only, post-screen; paid AI service,
spend bounded and measured).** The
LLM receives a **compact payload**: the constraint envelope + rooms with their
**verbatim stated dimensions** + the open questions. It must answer in strict JSON,
**citing room IDs and stated dimensions** (allocentric anchoring; no free-form
geometry): `[{change, target_room_id, rationale, cites_dimension, feasibility,
assumptions}]`. It proposes rework *plans* — it never reports measurements.

**Verifier — deterministic re-check (always on).** Every proposal is re-checked against
the graph + Tier-1 rules: size ≥ minimum for the proposed use? drainage adjacency real?
privacy sightline OK? route continuity preserved? cited dimension matches the annotation
**verbatim**? Verdict: `verified` / `rejected (reason)` / `downgraded to needs_survey`.
**Only verified proposals reach the scorecard, the cost engine, and the revised floor
plan** — the revised plan is drawn deterministically from verified proposals, never from
raw LLM output. Rejected proposals are logged and become regression-eval material.

**Spiked before built** (Stage 0 spike 5). Outcomes, recorded in writing: full two-tier
ships; or **deterministic-only ships** (Tier-1 rules + honest `needs survey` — still
useful); or the rework questions stay in the manual queue. No silent fallback.

### 5.6 As-is and potential plans — the plan model

Every property is modelled as **one as-is plan plus one or more potential plans**. The
"revised floor plan" in the UI is a potential plan, drawn deterministically — never a
free-form AI sketch.

**`AsIsPlan`** (assembled from `FloorPlanAnalysis` + `PhotoInference` +
`OutdoorAnalysis`): interior rooms with their **real (photo-inferred) door widths**,
verbatim dimensions, garage location, garden sketch / plot outline (satellite
post-confirmation), outdoor polygons. The measured truth the property is judged against.

**`PotentialPlan`** (one or more, per verified proposals + route geometry): the as-is
plan plus drawn changes — access routes, **ramps drawn to gradient with landings**,
**assumed soil-pipe routes**, new partitions, wet rooms. A property legitimately has
several ("option A: front ramp", "option B: side-passage route") — Q9's config mode is
really "which potential plans are feasible".

**Ramp geometry engine (deterministic, `route_geometry.py`).** A level-access fix is a
**drawn ramp**: rise (step count × step-height estimate) → run at 1:12 + landings
(1500 mm turning top and bottom, intermediate on direction changes) → footprint polygon.
The footprint is **snagged against every known boundary** — plot outline, driveway,
paths, side passage, building footprint, walls — each intersection a snag
(`conflicts with driveway — needs redesign`) or a pass. Verdict = footprint fits with
zero blocking snags; **snag list + drawn ramp are the output**.

**Recompute on boundary change (hard dependency).** Potential plans are functions of the
plot outline; any boundary change (garden sketch → satellite polygons, satellite
refresh) **recomputes all potential plans** (§5.4 rule 5). Each records the
`boundary_version` it was computed against.

**Soil-pipe routes are assumptions, and they can be load-bearing.** Plans do not show
soil stacks inside walls/floors; a proposed bathroom/WC marks an **assumed route** to
the nearest plausible connection (external wall for venting; shortest ground-floor run
to a likely stack), labelled `assumed — verify by survey`. **Robustness rule:** if the
verdict would *flip* when the assumption is wrong, the proposal must be `needs_survey`,
never `verified`. Assumption marking and the robustness rule are themselves evaluated
(`test_soil_route_evals.py`): on labelled houses with known stack positions — (a) route
plausibility ≥ 9 in 10, (b) load-bearing assumptions downgrade correctly ≥ 9 in 10.

### 5.7 How properties get ruled out — statuses, ordering, audit

Every property gets exactly one app status; **nothing is silently dropped** (trust).
The status model and the default ordering are the app's core value: with ~3,200
properties ingested at start, the first thing the user needs is the list ordered so the
best candidates are first.

| Status | Meaning | Decided by | Where it appears |
|---|---|---|---|
| `ruled_out` | a dealbreaker (D1–D5) fired | screen | **ruled-out view**: reason + evidence + **override (restore)** — never silently deleted |
| `pass` | screen passed, no contradiction, decision-ready | screen + contradiction check | main list, top |
| `pass_provisional` | screen passed but R2/R4 evidence pending, or a contradiction is under review | evidence pipeline | main list **below** confirmed passes, labelled |
| `needs_check` | can't decide: unreadable plan, missing photos where a rule needs them, indeterminate D4/D1-rescue | screen | needs-check queue, hidden from the main list by default |
| `no_plan` | no floor plan at all — metadata-only screen ran (D1 can still fire for flats without lifts) | payload | needs-check queue, sorted last |

**Default ordering (the value — "look here first"):**
1. **Decision-ready first**: `pass` ∧ within budget → sorted by **overall accessibility
   score descending**; tie-break most-recently-added. The score is labelled "score so
   far — R1 evidence" until R2/R4 fill the pending goals.
2. Then `pass_provisional`, score descending, labelled.
3. `needs_check` / `no_plan` live in the queue — out of the main list.

**Filters (defaults, all toggleable):** hide ruled-out (own view), **hide over-budget —
on by default** (the £200k cap), hide the queue. Sort keys switchable: score / all-in
price / added date (personas.md: sort depends on task).

**Audit & override:** ruled-out properties are never destroyed — the user can inspect
the reason + evidence and restore one. Restores are user state, not re-screens.

## 6. Staging — smallest MVP first, then value/effort order

**Ordering rule:** every release after the MVP is chosen as *most user value per unit of
additional effort*, constrained by dependencies (a release consumes earlier outputs,
never a later one). **Revision rule:** Stage 0 spike outcomes may reorder or cut
R2–R4; every revision is recorded in this doc in writing.

**Cost ladder (front-load the cheap):** AI is allowed in every stage; cost is managed by
ordering, not prohibition — metadata rules → local geometry → tiny local models → paid
AI (photos, survivors only) → free satellite tiles + local segmentation. A house is
ruled out or advanced by the cheapest evidence that can decide it; paid AI never runs on
a house a cheaper check already settled.

**The MVP (Release 1) — "Pass or fail — and does it fit £200k?"** attacks the two
biggest pains (trawling through houses that mostly fail; the per-house cost anxiety)
with the cheapest checks first and no LLM needed. Scorecard goals that need photos/satellite/rework
are shown as **`pending`**, never silently zero.

| # | Stage | User value | Effort | Depends on |
|---|---|---|---|---|
| 0 | Spikes & research | de-risks everything; labels; staging decision | high (one-off) | — |
| R1 | **MVP**: screen + plan score + £200k verdict, **in the app** | filters most houses; answers the money question | parser is the big cost; rest is rules + the UI slice | 0 |
| R2 | Real doors, steps, outside route | physical facts photos uniquely give; rescues step-houses | moderate; paid AI service | R1 |
| R3 | Can we rework it? + **address confirmation** | the rework-imagination pain; unlocks R4 | large + risk; spike-gated | R1 |
| R4 | Planning, parking, plot | strikes for conversion houses; outbuildings | medium; needs R3's confirmation UI | R1–R3 |

**The human verification gate — every stage.** Before any stage ships, Ashby
cross-checks a sample of its outputs against ground truth, per the sample table below.
Target agreement ≥ 9 in 10 (tuned after the first iteration); every disagreement becomes
a labelled corpus case; the loop is **fix → re-run evals → re-review**, iterating until
acceptable. The gate passes only on explicit sign-off, recorded in this doc. No stage
ships without it.

| Stage | Ashby cross-checks |
|---|---|
| 0 | spike corpora + go/no-go decisions (labels are Ashby-authored; sign-off recorded) |
| R1 | 10 plans' `FloorPlanAnalysis`; 25 `ScreenVerdict`s (all 5 rules); 10 scorecard/cost/affordability outputs vs hand review |
| R2 | 5–10 houses' assembled `PhotoInference` + Q19 verdicts (per-issue sets already labelled; this reviews the assembled output) |
| R3 | 5–10 houses' `ReworkProposals` (plausibility + verifier rejections) + potential plans (ramp snags, soil routes) |
| R4 | 5–10 houses' `PlanningAssessment` + `OutdoorAnalysis` |
| Final | end-to-end on 3–5 real listings |

---

### Stage 0 — Spikes & research

**Input:** existing ingestion path; ~20 real Rightmove listings (plans + photos); PRD §8
placeholder cost bands.

**Output:**
- **Eval scaffolding (prerequisite for every later gate)**: `tests/evals/conftest.py`
  (RUN_EVALS guard), `tests/evals/helpers.py` (corpus loader, shared thresholds),
  `make evals` target. Created here — R1's gate already runs it.
- **Payload extension (prerequisite for production runs)**: `POST /inject-property`
  gains `floor_plan_urls`, `photo_urls`, **`floor_level`, `has_lift`** (D1's metadata
  inputs). Corpus-based development can proceed before this lands; production runs
  cannot.
- **Model weights**: `make floorplan-model` runs here (~98 MB → `data/models/`).
- `tests/fixtures/floorplans/` + `labels.json`; `tests/fixtures/photos/` +
  `photo_labels.json`; aerial tile fixtures + labels (R4).
- `houses/accessibility/cost_table.py` — validated bands, flooring £/m² by covering
  type, planning/building-control fees folded in.
- **Planning data spike (Q18)**: permission-class table per fix; **heritage lookup
  verified live** (planning.data.gov.uk on ~20 sample coordinates; hit/miss/unknown
  rates, latency, no-auth); retained-parking rule confirmed against council guidance.
- Measured numbers recorded in this doc.

**Spikes (each with go/no-go, recorded in writing):**
1. **Model on 8GB laptop** — ≤ 5 s/plan, peak RAM ≤ 6 GB (else shrink + re-measure).
2. **OCR scale derivation** — ≥ 7 in 10 real plans readable.
3. **Door-width-from-photo** — banded method ≥ 8 in 10 on 10 labelled interior photos.
4. **Satellite feasibility (R4)** — outdoor polygon extraction ≥ 8 in 10 on 10 labelled
   aerial tiles; decides the satellite source.
5. **Rework feasibility (§5.5)** — 5–10 houses with **reference rework answers authored
   by Ashby**: (a) Tier-1 rule coverage; (b) Tier-2 proposal plausibility rated by
   Ashby; (c) **verifier catch rate on injected hallucinations**; (d) AI-service cost
   per house.
   Decides two-tier vs deterministic-only vs manual queue.
6. **Plan-model spike (§5.6)** — (e) ramp snagging ≥ 8 in 10 on labelled plots;
   (f) soil-route assumption plausibility ≥ 8 in 10, load-bearing detection ≥ 9 in 10.

**Gate:** spikes 1–6 pass (or the plan changes in writing); every cost band cites ≥ 1
published UK source; **Ashby sign-off on spike outcomes and the resulting staging order**
(human verification gate).

---

### Release 1 — MVP: pass or fail, and does it fit £200k?

**Purpose:** filter out dealbreaker houses (Q1–Q5) with the cheapest checks first, score
the survivors from the plan, answer the money question (Q17), and **show it all in the
app** — the sorted list and click-through scorecard are the MVP's user surface, not a
later phase. Everything the plan can't know yet is shown `pending`, not guessed.

**Input:** extended payload (plan + photos + `floor_level`/`has_lift`); Stage 0 corpus
+ weights + cost table.

**Contents:**
- Plan parser (`houses/floorplan/{read,segment,geometry,graph}.py`) → `FloorPlanAnalysis`
  (annotations verbatim, graph, garden sketch, estimates, scale).
- Screen (`houses/accessibility/screen.py`) → `ScreenVerdict` for Q1–Q5; D1 uses the
  local step-count classifier (R1 includes it — a tiny local model, cheap, no paid AI
  needed for screening).
- Scorecard (plan-derived goals only: Sleeping presence/convertibility Q6, Bathing
  presence Q7, Dimensions Q11, Main-house access Q16, Storage Q10; photo/satellite
  goals Q8/Q12/Q13/Q14/Q15/Q19 = `pending`).
- Cost engine + affordability: plan-derivable fixes (door widening flagged `pending`
  pending photo widths; entrance ramp costable from D1's step count) → total vs £200k.
- **App UI slice (first-class in R1, not deferred)**: score badge + **default ordering
  per §5.7** (decision-ready first, score descending, within budget; provisional below;
  queue hidden), sort keys switchable (score / all-in price / added date), filters with
  sensible defaults (hide over-budget, hide queue), detail scorecard with evidence
  links and the annotated-plan overlay, **ruled-out view with reasons + restore**, and
  the needs-check queue. This is the interview's core flow — *sort by overall score
  descending, then click through to the detail to see the scorecard* — and the MVP is
  worthless without it.
- **App-only delivery**: the spreadsheet is not touched (§8); `test_no_sheet_writes.py`
  enforces it.
- `tests/unit` + eval files for the R1 outputs; registry-coverage test.

**Output:** `ScreenVerdict`, `FloorPlanAnalysis`, plan-derived `AccessibilityScorecard`,
`CostEstimate` + affordability verdict.

**Gate (technical):** Phase-gate evals from the matrix (annotations 19/20; estimates
within 10 cm; topology ≥ 9/10; screen rules ≥ 9/10 with **zero false fails**; cost
within 15% of hand pricing; 199/200/201k boundary); `make test` + `make evals` green;
**batch run over the existing dataset (~3,200 properties)**: the app shows the ordered
list (§5.7) with screen latency, rule-out counts and queue counts recorded — the
ordering is the deliverable. **Human verification gate:** Ashby cross-checks 10
analyses, 25 verdicts, 10 cost/affordability outputs; iterate until sign-off.

---

### Release 2 — Real doors, steps, outside route

**Purpose:** pull the physical facts photos uniquely give (Q12–Q15) and unlock the D1
rescue (Q19). Highest remaining value per effort: door widths and steps are facts users
cannot get any other way, and the rescue unlocks step-houses.

**Input:** R1 outputs (graph drives crop selection); listing photos; OpenRouter provider
(exists).

**Contents:** `houses/photo_infer/{door_width,patio_doors,thresholds,floor_covering}.py`
→ `PhotoInference`; Q19 passage + rear-access from photos; contradiction rule (photo
evidence contradicting a screen pass → needs-check, never silent, never auto-fail);
`houses/accessibility/route_geometry.py` early slice: passage-width bands feed Q19.

**Output:** `PhotoInference`; Q19 verdicts incl. the D1 rescue path.

**Gate (technical):** per-issue evals ≥ 9 in 10 (door bands, patio ID, thresholds,
steps, flooring); Q19 passage/rear-access/rescue ≥ 9 in 10; AI-service cost per house
recorded. **Human verification gate:** Ashby reviews 5–10 houses' assembled photo
inference + Q19 verdicts; iterate until sign-off.

---

### Release 3 — Can we rework it?

**Purpose:** the rework-imagination pain — turn the constraint envelope into actual
proposals and drawn potential plans (ramps snagged, soil routes assumed), per the Stage
0 spike outcome (§5.5, §5.6).

**Input:** R1 outputs; OpenRouter; rework spike outcome; `AsIsPlan` assembly.

**Contents:** `houses/accessibility/rework.py` (Tier-1 rules + verifier),
`proposals.py` (Tier-2 LLM layer), `route_geometry.py` (ramp engine + snagging),
`soil_routes.py` (assumed routes + load-bearing check), `revised_plan.py` (potential
plan rendering); `AsIsPlan`/`PotentialPlan` assembly; config mode depth (Q9 = which
potential plans are feasible).
- **Address-confirmation UI (lands here, R4 depends on it)**: "is this the right
  house?" with geocode + map preview; confirming triggers the post-confirmation
  enrichment pass. It also feeds R3's own potential plans via the recompute rule —
  confirmed boundaries mean real ramp footprints.

**Output:** `ReworkProposals` (verified only), `AsIsPlan`, `PotentialPlan`s; revised
floor plan on the detail page.

**Gate (technical):** per spike outcome — two-tier: verifier rejects ≥ 9 in 10
hallucinated proposals, verified proposals ≥ 8 in 10 plausible; deterministic-only:
Tier-1 verdicts ≥ 9 in 10, rest `needs survey`. Ramp footprint + snags ≥ 9 in 10 on
synthetic plots; recompute-on-boundary-change returns a new footprint. Soil-route
plausibility ≥ 9 in 10; load-bearing assumptions downgrade ≥ 9 in 10. **Human
verification gate:** Ashby reviews 5–10 houses' proposals + potential plans; iterate
until sign-off.

---

### Release 4 — Planning, parking, plot

**Purpose:** the planning/building-control strikes (Q18), retained parking, outbuildings
(Q9), and refined garden quality (D3) — via satellite after address confirmation.

**Input:** R1–R3 outputs; **the address-confirmation UI from R3** (production runs only
for confirmed addresses — build and evals use aerial fixtures first, so R4 is never
blocked waiting for real user input); Stage 0 planning/heritage + satellite spike
outcomes.

**Contents:** `houses/satellite/outdoor.py` → `OutdoorAnalysis`; heritage lookup
(`planning.data.gov.uk`); permission-class rules; parking check (driveway footprint);
approval-likelihood banding; post-confirmation enrichment re-run wiring (two-pass §5.4,
recompute rule 5); scorecard refinements for the newly-final items.

**Output:** `OutdoorAnalysis`, `PlanningAssessment` + `HeritageLookup`, refined
scorecard items.

**Gate (technical):** outdoor polygons ≥ 9 in 10; permission classes ≥ 9 in 10; parking
≥ 9 in 10; heritage flags ≥ 9 in 10 vs labelled coordinates; planning strikes never
auto-reject (asserted). **Human verification gate:** Ashby reviews 5–10 houses'
planning + outdoor analyses; iterate until sign-off.

---

### Final — Acceptance run & docs

**Gate:** run on N real listings: screen latency, **screening uses only free local
checks — paid AI is never required to rule a house out**, **zero wrongly-rejected
houses**, needs-check rate, photo-inference AI-service cost per survivor, `unknown`
rates per issue type — all recorded in this doc. `docs/enrichment-modules.md`
updated; this doc updated with build results. **Ashby end-to-end sign-off** on 3–5 real
listings (human verification gate).

## 7. Data model (definitive schemas)

### 7.1 `FloorPlanAnalysis` (R1 output)

```python
@dataclass(frozen=True)
class FloorPlanAnalysis:
    annotations: tuple[Annotation, ...]   # {text: str (verbatim), metres: float,
                                          #  spans: (room_id, wall_pair), label: "as_stated_on_plan"}
    graph: FloorPlanGraph                 # rooms V (id, label, kind, polygon),
                                          #  doorways E (id, connects, position_only),
                                          #  garden sketch (presence only, estimated)
    scale_m_per_px: float | None          # None → plan unreadable → needs_check
    estimates: dict[str, Estimate]        # hallway_clear_width_mm, turning_circle_mm,
                                          #  door_positions, unannotated_spans
                                          #  → all label: "estimated_from_plan"
```

### 7.2 `ScreenVerdict` (R1 output)

```python
@dataclass(frozen=True)
class ScreenVerdict:
    verdict: Literal["pass", "fail", "needs_check"]
    fails: tuple[ScreenFail, ...]         # stops at first tripped rule
    # ScreenFail = {rule: "D1".."D5", question: "Q1".."Q5", reason: str (plain English),
    #               evidence_urls: tuple[str, ...]}
```

### 7.3 `PhotoInference` (R2 output)

```python
@dataclass(frozen=True)
class PhotoInference:
    doors: tuple[DoorInference, ...]      # {id, kind: interior|entrance|patio|side,
                                          #  clear_width_band: "<700"|"700-780"|"780-850"|">850"|"unknown",
                                          #  threshold: "flush"|"single_step"|"multi_step"|"unknown",
                                          #  step_count: int | None, evidence_urls}
    openings: tuple[OpeningInference, ...] # {id, kind: "patio"|"window"|"unknown", evidence_urls}
    floor_coverings: dict[str, str]        # room_id → "carpet"|"hard"|"unknown"
    passage: PassageInfo | None            # {present, width_band, gate, surface,
                                           #  rear_door: bool|None, rear_level: bool|None}  (Q19)
    # all values label: "inferred_from_photo"
```

### 7.4 `AccessibilityScorecard` (R1, refined R2–R4)

```python
@dataclass(frozen=True)
class AccessibilityScorecard:
    overall: float                        # 0–100
    goals: tuple[GoalScore, ...]          # Entering|Sleeping|Bathing|Sanctuary|Outdoor|
                                          #  MainHouseAccess|Storage|Dimensions|Flooring|
                                          #  PlanningAndApprovals
                                          # {score, checks, evidence_urls, part_m, label,
                                          #  status: "scored"|"pending"}
    config_mode: Literal["annexe", "main_suite", "both", "unknown"]
    pending: tuple[str, ...]              # goals awaiting R2–R4 evidence — never silently zero
```

### 7.5 `CostEstimate` (R1, refined R2–R4)

```python
@dataclass(frozen=True)
class CostEstimate:
    items: tuple[CostItem, ...]           # {fix, band_low_gbp, band_high_gbp, driver, evidence_urls}
    needs_survey: tuple[str, ...]         # excluded from total, never silent
    pending: tuple[str, ...]              # fixes awaiting photo/satellite evidence (R2/R4)
    total: Money                          # sum of items only; flooring = £/m² × plan area
```

### 7.6 `PlanningAssessment` (R4 output — Q18)

```python
@dataclass(frozen=True)
class PlanningAssessment:
    per_fix: tuple[FixPermission, ...]    # {fix, permission: "none"|"building_control_only"|
                                          #  "planning_required", rationale, evidence_urls}
    heritage: HeritageLookup              # see below
    garage_parking_ok: bool | None        # None → can't tell → "needs checking"
    approval_likelihood: Literal["high", "medium", "low", "unknown"]
    strikes: tuple[str, ...]              # plain-English flags; NEVER auto-fails

@dataclass(frozen=True)
class HeritageLookup:
    listed: bool | None                   # None → lookup miss → "needs checking"
    conservation_area: bool | None        # None → coverage unverified → "needs checking"
    coverage: Literal["verified_good", "patchy", "unknown"]
    sources: tuple[str, ...]              # planning.data.gov.uk entity URLs + NHLE list-entry URLs
```

### 7.7 `OutdoorAnalysis` (R4 output — post-address-confirmation only)

```python
@dataclass(frozen=True)
class OutdoorAnalysis:
    polygons: dict[str, Polygon]          # driveway, garden, passage, outbuildings
    passage: PassageInfo | None           # satellite refinement of Q19
    driveway_for_parking: bool | None     # Q18 input
    pass_label: Literal["pre_confirmation", "post_confirmation"]
```

### 7.8 `ReworkProposals` (R3 output — §5.5)

```python
@dataclass(frozen=True)
class ReworkProposal:
    change: Literal["add_bathroom", "convert_garage", "wet_room", "new_partition",
                    "widen_door", "ramp", "reposition_door"]
    target_room_id: str
    rationale: str                        # plain English
    cites_dimension: str | None           # verbatim plan text it relies on
    feasibility: Literal["verified", "rejected", "needs_survey"]
    survey_items: tuple[str, ...]         # ceiling height, load-bearing, floor level, soil stack…
    evidence_urls: tuple[str, ...]

@dataclass(frozen=True)
class ReworkProposals:
    verified: tuple[ReworkProposal, ...]  # ONLY verified proposals reach scorecard/cost/revised plan
    rejected: tuple[ReworkProposal, ...]  # logged → regression-eval material
```

### 7.9 As-is and potential plans (§5.6)

```python
@dataclass(frozen=True)
class AsIsPlan:                           # assembled truth for judging the property
    rooms: tuple[Room, ...]               # incl. real photo-inferred door widths
    plot_outline: Polygon | None          # satellite post-confirmation
    garage: GarageInfo | None             # location, contiguity, own-access potential
    outdoor: OutdoorAnalysis | None

@dataclass(frozen=True)
class RampGeometry:
    rise_mm: float
    run_mm: float
    gradient: float                       # 1:12
    landings: int
    footprint: Polygon
    snags: tuple[RampSnag, ...]           # {obstacle: str, intersects: bool,
                                          #  severity: "blocking"|"partial", note}
    verdict: Literal["feasible", "infeasible", "needs_redesign"]

@dataclass(frozen=True)
class SoilRouteAssumption:
    from_room: str
    to_connection: str
    assumed_path: LineString
    load_bearing: bool                    # wrong assumption would flip the verdict?
    label: Literal["assumed", "verified"]

@dataclass(frozen=True)
class PotentialPlan:
    id: str
    name: str                             # "option A: front ramp"
    as_is_ref: str
    changes: tuple[ReworkProposal, ...]   # verified only
    ramps: tuple[RampGeometry, ...]
    soil_routes: tuple[SoilRouteAssumption, ...]
    boundary_version: str                 # boundary knowledge it was computed against
    pass_label: Literal["pre_confirmation", "post_confirmation"]
```

## 8. Delivery surface — the app only (the spreadsheet is off-limits)

**The spreadsheet MUST NOT be touched** — no new columns, no writes, no formula sync.
This feature delivers through the app (list, detail, needs-check queue) only.
`test_no_sheet_writes.py` (runs in `make test`) asserts that no accessibility module
imports `houses.sheets` or writes a row — the constraint is enforced, not just stated.

## 9. Deliverables

- `houses/floorplan/{read,segment,geometry,graph}.py`, `houses/photo_infer/`
  `{door_width,patio_doors,thresholds,floor_covering}.py`,
  `houses/satellite/outdoor.py`,
  `houses/accessibility/{screen,cost_table,eval_thresholds,planning_rules,heritage_lookup,revised_plan,rework,proposals,route_geometry,soil_routes}.py`,
  `houses/nodes/accessibility.py`
- `houses/services.py` protocol + default + fake, `tests/helpers.py`
- `tests/evals/{conftest,helpers}.py` — one suite per registry row:
  `test_entrance_evals.py` (Q1), `test_bathroom_evals.py` (Q2, Q5),
  `test_outdoor_evals.py` (Q3, Q13, Q14), `test_bedroom_evals.py` (Q4, Q6),
  `test_wetroom_evals.py` (Q7), `test_sanctuary_evals.py` (Q8),
  `test_config_evals.py` (Q9), `test_route_evals.py` (Q10, Q16),
  `test_annotation_geometry_evals.py` (Q11 + components),
  `test_door_width_evals.py` (Q12), `test_floor_covering_evals.py` (Q15),
  `test_affordability_evals.py` (Q17), `test_planning_evals.py` (Q18),
  `test_external_route_evals.py` (Q19, incl. the D1 rescue path),
  `test_rework_evals.py` (§5.5), `test_route_geometry_evals.py` (§5.6),
  `test_soil_route_evals.py` (§5.6), `test_end_to_end_evals.py`
- `tests/unit/test_registry_coverage.py` — registry ↔ eval linkage (runs in `make test`)
- `tests/unit/test_no_sheet_writes.py` — the spreadsheet ban, enforced (runs in `make test`)
- `tests/fixtures/floorplans/` + `labels.json`, `tests/fixtures/photos/` +
  `photo_labels.json`, aerial tile fixtures + labels
- `POST /inject-property` payload extension (no sheet changes)
- API endpoints + Vue list/detail changes
- `tools/accessibility/{label_corpus,batch}.py`, Makefile targets
  (`make evals`, `make evals-incremental`, `make floorplan-model`, `make access-screen`,
  `make access-analyze`, `make access-validate`)
- This doc updated with gate results, human-gate sign-offs, and any post-spike
  re-staging (commute-doc style)

## 10. Deferred

- Rightmove search-level accessibility filter integration.
- Multi-user validation (weights, dimension profiles, Q4 determinability,
  floor-covering classes for other disability profiles).
- Financing the conversion cost into the monthly affordability model (out of PRD scope).
- Better-than-banded door widths (e.g. surveying photos with metric calibration) — only
  if R2 bands prove insufficient.
- Multi-option potential-plan comparison UI (R3 renders one verified plan first;
  side-by-side options later).
