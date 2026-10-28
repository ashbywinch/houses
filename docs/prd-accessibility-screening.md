# PRD — Accessibility Screening & Retrofit Costing

**Status:** Draft — informed by a single-participant user interview (the primary user, a
wheelchair user). Validate with 2–3 more disabled users before locking weights and
dealbreaker wording (see [Open questions](#open-questions)).
**Audience:** product + engineering decision-makers.
**Related:** [implementation-plan-accessibility-screening.md](implementation-plan-accessibility-screening.md),
[personas.md](personas.md) (Ashby), [rightmove-commute-monitor.md](rightmove-commute-monitor.md)
(the ingestion/search pipeline this plugs into).

## 1. Problem

Most Rightmove listings do not meet the user's accessibility requirements, so they must
trawl through a large number of properties. For every house that survives the first pass,
they repeat the same expensive mental work:

> "…it's a chore to sit and imagine which part of the space I could use, how I might
> reconfigure the space, whether the plumbing changes would be feasible/realistic and how
> much it would all cost to redo. And I have to do that for every house!"

Rightmove offers no accessibility field, floor plans rarely carry usable dimensions, and
photos systematically hide steps and thresholds. Dealbreaker properties are only
identified by visiting or by exhausting agent calls. The house-hunting spreadsheet
(Properties View) enriches commutes, schools, and costs — but not access.

## 2. User profile (interview summary, 2026-08-04)

| Dimension | Profile |
|---|---|
| Mobility | Power chair for distance; rolling stool for short indoor work; ambulatory only for the last couple of metres, sparingly (**energy is a daily budget** — forced walking adds up) |
| Storage | Power chair + stool need homes that don't block circulation; chair needs a charging point |
| Goal | A private "sanctuary" suite: bedroom + private bathroom + kid/dog-excludable sitting space with a view |
| Configuration | Annex (garage conversion / outbuilding) **or** main-house ground-floor suite — tool must surface which is feasible, or both |
| Money | "Nothing is a dealbreaker if I can fix it with money." Cost precision is out of scope; the **decision is under/over a £200k conversion-cost cap** |
| Trust | Distrusts opaque automation (shared with all personas, see personas.md). Every claim must be evidenced back to photo, floor plan, or proposed revised floor plan; link to Part M |
| Trade-offs | Legitimate: one house was forgiven its lack of private outdoor access because of a spacious view-bedroom — the exception, not the rule. Output must surface per-goal strengths, not bury them |

## 3. Goals

1. **Rule out dealbreaker houses ASAP**, cheapest checks first — the screen runs on
   free local checks (metadata rules, plan geometry, tiny local models) and never needs
   paid AI to rule a house out; it works the moment a listing is scraped, with zero
   user input.
2. For survivors, **score against an accessibility checklist** (some goals hard-fail, most
   scored) and produce a per-house **conversion-cost estimate**.
3. **Automatically compute affordability** from the conversion cost (all-in capital vs the
   user's budget cap).
4. **Order the list so the best candidates come first** — overall score descending
   among decision-ready, within-budget properties; provisional and undecidable
   properties below or in a queue; click through to a **scorecard detail**.
5. **Evidence every claim** back to a source (photo, floor plan, proposed revised floor
   plan) with Part M references, and mark AI-uncertainty explicitly.
6. **Flag planning/building-control requirements** for any proposed work and score how
   likely approval is — a strike against the property, never a dealbreaker (e.g. garage
   conversions fail planning when not enough off-street parking remains).

## 4. Non-goals

- Precise quotes or line-item accuracy — cost bands only, decision is under/over the cap.
- Building-control compliance certification — decision support, not a survey.
- Covering every room of the house — focus is the suite, the route to it, and the bonus
  items below.
- Rightmove search-filter integration or viewing booking (deferred).
- Accessibility for other disability profiles — dimensions are configurable (see 7).
- **Any change to the spreadsheet** — no new columns, no writes; the app is the only
  delivery surface.

## 5. The two-layer model

| Layer | Purpose | Cost | Failure semantics |
|---|---|---|---|
| **1. Dealbreaker screen** | Kill un-fixable houses before analysis | **Cheapest checks first**: metadata rules → local geometry → tiny local models. AI allowed everywhere, but paid AI is never needed to rule a house out | Hard fail — removed immediately, listed with reason |
| **2. Scorecard + costing** | Score survivors, price the fixes, compute affordability | Local floor-plan parsing (CPU segmentation + OCR + deterministic geometry); optional photo VLM later | Scored; trade-offs allowed |

Dealbreaker rule: *"nothing is a dealbreaker if I can fix it with money."* A dealbreaker is
therefore a barrier **no amount of money removes** (site position, structure, layout
impossibility). Anything fixable-with-money is a scored goal with a cost band.

Three classes, decided by that rule:
- **Dealbreaker** — cannot be fixed with money; auto-removed by the screen, reason shown
  (Q1, Q2, Q3, Q4, Q5-privacy).
- **Scored factor** — fixable with money, or a quality/risk that varies; contributes to
  the score, trade-offs between factors are legitimate (Q5-en-suite, Q6–Q16, Q18).
- **User filter** — the user's own knob, applied after scoring, never an automatic fail
  (Q17: affordability — the £200k cap is a configurable setting, not a dealbreaker).
- **Planning/building-control requirement = a strike, never a dealbreaker** (Q18):
  requiring permission or a low approval likelihood lowers the score and is flagged;
  it never auto-rejects.

The authoritative, testable checklist — every personal question from the research,
traced to its verdict computation, its class, and its own labelled eval suite — is the
**question registry** in [implementation-plan-accessibility-screening.md](implementation-plan-accessibility-screening.md) §5
(Q1–Q18). New personal questions enter the registry first (with a class and an eval),
then get implemented; a question without evals doesn't ship.

## 6. Dealbreaker checklist (screen)

All five are hard fails when **determinable**. What cannot be determined from available
data must be surfaced as "needs check" and routed to a manual queue — **never auto-failed
on a guess** (false fails destroy trust faster than false passes).

| # | Dealbreaker | What makes it fail | Evidence source |
|---|---|---|---|
| D1 | **No level-access route possible** | Entrance up >~1 step with **no alternative level route** (side/rear entrance, or a front-to-back outside route where **level access at the rear is feasible** — Q19; a blocked front door alone doesn't fail a house with a fully evidenced alternative); flat/apartment with no lift | Metadata (floor level), entrance photo step-count classifier, Q19 external-route + rear-access evidence |
| D2 | **No downstairs bathroom feasible** | No ground-floor WC/bathroom **and** no plausible location to add one (no room ≥ ~2.2×1.5 m against an external/plumbing wall) | Floor-plan graph + OCR labels |
| D3 | **No semi-private outdoor space** | No rear/private outdoor area reachable — only car park or bin alley | Floor plan (garden boundary), map context, photos |
| D4 | **Bedroom noise exposure** | Accessible-bedroom candidate wall/window directly adjoins the only family outdoor seating area | Floor plan + garden-use inference (photos/map) — **hard-fail only when determinable**; otherwise "needs check" |
| D5 | **Bathroom privacy infeasible** | Only ground-floor WC position is directly sightlined from the front door (the "loo facing the front door" trap) with no alternative location | Floor-plan graph (door/room adjacency) |

Fail-fast: exit the screen at the first hard fail, cheapest checks first (D1 metadata →
D2/D3/D5 plan geometry → D4 photos).

Everything **not** in this table is a scored factor or a user filter — never an automatic
fail: affordability (Q17, the £200k cap) is a user filter; planning/building-control
requirements (Q18) are a strike that lowers the score, never a rejection.

## 7. Scored goals (scorecard)

Weights are defaults, configurable (see 10). Dimensions follow UK Building Regulations
Part M4(3); the **power-chair profile** may raise the turning-circle default — open
question, see 10.

| Goal | Checks | Part M reference | Weight |
|---|---|---|---|
| **Entering** | Entrance door clear opening ≥ 850 mm; threshold ≤ 15 mm; power-chair route entrance → suite; **a ramp is a drawn plan element** — 1:12 gradient, landings, footprint snagged against plot outline, driveway, paths, passage (intersections are flagged, the calculation reruns whenever boundary knowledge changes). **Door widths are judged from photos, not the plan** — agents annotate rooms wall-to-wall, never doors. An **alternative level route** (side/rear entrance, front-to-back outside route, Q19) counts — it rescues a blocked front door | M4(3) entrance/door/threshold | High |
| **Sleeping** | Ground-floor bedroom achievable; quiet placement (see D4); view from bedroom | M4(3) bedroom | High |
| **Bathing** | Level-access wet room or accessible WC space feasible (Q7); **private to access, ideally en suite** — privacy is a dealbreaker (Q5), en-suite is a scored quality | M4(3) sanitary | High |
| **Sanctuary** | Kid/dog-excludable sitting space with a view — indoors, private | — (user-defined) | High |
| **Outdoor** | Semi-private outdoor space, level access (patio door) — quality scored, not just presence (D3 is presence). **Patio-door vs window identification, and step counts to every exterior door, from photos; garden quality and outbuildings refined from satellite after the address is confirmed in the app** | M4(3) outdoor access | Medium |
| **Main-house access (bonus)** | Level route to rest of ground floor incl. washing machine/kitchen | — | Low (bonus) |
| **Storage** | Power-chair parking + charging, stool parking — out of circulation | — | Medium |
| **Dimensions** | Hallway ≥ 1050 mm head-on / 1200 mm at turns; turning circle ≥ 1500 mm (profile-dependent); door clear openings ≥ 850 mm on route. Unannotated spans are labelled *estimated from plan* | M4(3) circulation | High |
| **Flooring** | Per-room covering (carpet / hard / unknown) from photos — rolling comfort; replacement cost = £/m² × plan-stated area | — (user-defined) | Medium |
| **Planning & approvals** | Per-fix permission class (none / building-control-only / planning-required); approval-likelihood band; garage conversions check retained off-street parking; listed/conservation flags. **A strike, never a dealbreaker** | Part M is the building-control route for most fixes | Medium |

**Configuration mode** is an explicit output, not an assumption: `annexe feasible` /
`main-house suite feasible` / `both`. Decided by: garage conversion potential (garage
contiguous to main living, under main roofline, own access possible) vs ground-floor
suite potential (bedroom + bathroom + sitting space on one level).

## 8. Costing & affordability

- **Cost bands** per required fix (illustrative, validated in Phase 0 of the
  implementation plan): ramp £1.5–4k; door widening £0.5–1.5k; hallway widening £5–15k
  (structural); wet-room conversion £8–15k; new downstairs WC £8–20k (plumbing route
  dependent); garage conversion £25–60k; patio levelling £2–8k; threshold £0.3–1k; chair
  storage + charger £0.5–2k.
- Each band shows its **driver** ("depends on ground type", "depends on drainage route")
  so the range is sanity-checkable — never a bare number.
- Structural-only items get costed; everything else is flagged **"needs survey"** and
  excluded from the total (never silently included).
- **Affordability** = listing price + conversion-cost total vs the user's budget cap
  (default £200k, configurable). The output is under/over + the all-in figure — not a
  monthly affordability model (explicitly out of scope).

## 9. Presentation & workflow

1. **Screen** runs as listings arrive (or on demand): dealbreakers removed ASAP,
   cheapest checks first; each removal cites the failing rule + evidence.
2. **App list — the value is the ordering** (≈3,200 properties start; click-through is
   secondary to a good "look here first" order):
   - Every property has one status: `ruled_out` (dealbreaker, shown in a
     **ruled-out view with reason + evidence + restore** — never silently dropped),
     `pass` (decision-ready), `pass_provisional` (R2/R4 evidence pending or a
     contradiction under review), `needs_check` / `no_plan` (queue, hidden by default).
   - **Default order**: `pass` ∧ within budget first, sorted by **overall
     accessibility score descending** (tie-break newest); provisional below, labelled;
     queue out of the way.
   - **Filters with defaults**: hide over-budget (on), hide the queue (on), hide
     ruled-out (own view). Sort keys switchable: score / all-in price / added date
     (personas.md: sort depends on task).
   - Affordability column (all-in vs cap) visible on every card.
3. **Detail scorecard**: per-goal scores with evidence links — annotated floor plan
   (measured doorways/hallways highlighted), photo crops, **the as-is plan and one or
   more potential plans** (e.g. "option A: front ramp", "option B: side-passage route");
   ramps are drawn to gradient with landings and their **intersections with the plot
   flagged**; **soil-pipe routes are marked `assumed — verify by survey`**; every
   potential plan is **drawn only from rework proposals verified against the measured
   plan** — an AI suggestion that fails verification never appears on it; Part M clause
   linked per check.
4. Every claim carries a source link; measurements carry one of three labels —
   `as stated on the plan` (the dimension text quoted **verbatim** — the plan's own
   numbers are never "corrected" or re-measured), `estimated from plan` (computed from
   plan geometry, e.g. hallway widths, turning circles), or `inferred from photo`
   (door widths, thresholds, step counts, patio doors, floor coverings). Unknowable
   items show `unknown` / "needs survey" — never a guess presented as fact.
5. Ambiguous plans (OCR confidence low, segmentation IoU low) → **"needs check" queue**,
   never auto-rejected.
6. **Two-pass refinement**: the screen + initial score run on plan + photos alone.
   Outdoor-dependent verdicts (garden quality, outbuildings, retained parking,
   front-to-back passage width) go **provisional until the user confirms the property's
   address/location in the app**, then a satellite pass refines them. Every claim is
   labelled with which pass produced it.

### Delivery staging & human verification

The feature ships in value-ordered releases (full staging in the implementation plan §6):
- **R1 MVP — "Pass or fail, and does it fit £200k?"**: dealbreaker screen (Q1–Q5), plan-derived
  scorecard, plan-derivable cost + affordability verdict — **including the app UI slice**
  (sorted list, click-through scorecard, needs-check queue: the interview's core flow;
  the UI is not a later phase). Goals needing photo/satellite/rework
  evidence show as **pending**, never silently zero.
- **R2 — Real doors, steps, outside route** (photo inference Q12–Q15, Q19 rescue).
- **R3 — Can we rework it?** (verified rework proposals + drawn potential plans).
- **R4 — Planning, parking, plot** (satellite + Q18, post-address-confirmation).

**Human verification gates:** every release ships only after Ashby cross-checks a
defined sample of its outputs against ground truth (10 plan analyses, 25 screen
verdicts, 5–10 scorecards/proposals per release) and signs off — disagreements become
labelled cases, the loop iterates until acceptable. Spike outcomes may reorder or cut
releases; revisions are recorded in writing.

## 10. Open questions (validate with 2–3 more disabled users)

1. Turning-circle default for the power-chair profile — Part M's 1500 mm vs a larger
   preset. Needs a real measurement session; configurable either way.
2. Goal weights — does Sleeping outweigh Entering? Are bonuses worth scoring at all?
3. D4 (noise) determinability — without photos of garden use, when is it safe to hard-fail?
4. Cost bands need market validation (Phase 0) — the numbers above are placeholders.

## 11. Acceptance criteria

1. Screen removes a dealbreaker house in < ~5 s per listing, using only free local
   checks — paid AI is never required to rule a house out.
2. On a curated set of known-pass / known-fail houses, **zero false fails**; false passes
   routed to the "needs check" queue, not silently kept.
3. Survivors get: overall score, per-goal scorecard, configuration mode, conversion-cost
   total + affordability verdict, and planning/approval flags (permission class,
   approval likelihood) for any proposed work.
4. 100% of scorecard claims link to a source (photo / floor plan / revised plan) and a
   Part M clause where one exists; every measurement carries its
   as-stated / estimated / photo-inferred label.
5. List sorts by score; detail renders scorecard with evidence.
6. Every accessibility-issue type (annotation reading, hallway width, turning circle,
   door position, door width, patio-door ID, threshold, step count, floor covering,
   screen rules, cost total) has its own labelled eval suite — an issue type without
   evals doesn't ship.
7. A batch run over the existing dataset shows the ordered list (decision-ready first,
   score descending, within budget), a ruled-out view with reasons + restore, and the
   needs-check queue; screen latency and rule-out/queue counts recorded.
