# Usability Walkthrough — Two-Phase Prompt

Repeatable usability process for the Houses app, in TWO phases with SEPARATE
prompts. Phase 1 collects raw participant experience (thinking-aloud +
screenshots) with ZERO priming. Phase 2 evaluates that evidence and produces
confusions + recommendations. **The phase-2 prompt must never be shown to the
phase-1 participant** — it would poison the walk.

Baseline for judging findings: [docs/usability-requirements.md](usability-requirements.md).

## Parameters (update per run)

- App URL: `http://127.0.0.1:5173/` (frontend; backend `http://127.0.0.1:8765`).
- Auth: the browser may already hold a session. If a "Sign in with Google"
  page appears, set the session cookie first: read the value from
  `/tmp/cookie.txt` and apply it with
  `page.setCookie({name: 'session', value: '<value>', domain: '127.0.0.1', path: '/'})`,
  then reload.
- Screenshots: the participant saves screenshots into the browser's
  screenshot directory with descriptive filenames and writes a manifest
  (path + what it shows). The evaluator reads the manifest — it does not
  regenerate screenshots.

---

## Phase 1 — Usability test (participant)

Run this with a participant agent that can drive the browser and take
screenshots. The participant role-plays a first-time, non-technical house
buyer. **The briefing below is the ONLY information they get about the app.**

### Participant briefing (give verbatim, nothing more)

> You're helping your family buy a house. Two things about your situation:
> you recently moved office, and you recently sold your previous house —
> for less than you'd expected. Use this website to find houses and figure
> out two things: how long the commute would be, and what you'd pay each
> month. The commute you see on the front page is to your old office, and
> the monthly cost on the houses looks high but you don't know why. There's
> no wrong way to use it. Please talk out loud the whole time — say what
> you're trying to do, what you expect to happen, and anything that confuses
> you, even small things.

### Behaviour rules (for the agent, not the participant's copy)

- **The briefing is the ONLY information about the task** — the situation
  facts (moved office, sold for less, high-looking monthly cost) are the
  participant's role; do not add to them.
- **Do NOT reveal anything about the app** — no mention of settings pages,
  editing commutes, selling a home, deposits, mortgages, thresholds, or any
  feature or finding. Do not tell them what exists or where things are.
- **Think aloud, verbosely**: narrate intent before every action ("I want to
  see what I'd pay each month"), expectations ("I'd expect an edit button
  here"), and confusion as it happens. This is the raw experience, not a
  report for an audience.
- **Say it if it looks odd or confusing** — anything at all: a number that
  looks wrong, a label that reads oddly, a missing button, wording that
  makes you pause, something you expected to find and couldn't, a state you
  can't express. Include things you merely suspect are off. Minor > nothing.
- **Screenshots**: take one at each notable screen and before/after each
  action you try; name them descriptively (`01-main-screen.png`,
  `02-trying-to-edit-commute.png`). Keep a manifest: `path | what it shows |
  what you were trying to do`.
- **Do not recommend fixes** — experience only.
- **Do not save anything**: explore and click freely, but never press a
  button that saves or submits a change.

### Phase-1 output

1. The full thinking-aloud transcript (verbatim, in the first person).
2. The screenshot manifest with paths.
3. The final state: what you were able to achieve, what you gave up on, and
   where you stopped.

---

## Phase 2 — UX evaluation (evaluator)

Run this with a designer/evaluator agent. Input: the phase-1 output
(transcript + screenshot manifest). The evaluator does NOT re-walk the app
to generate findings — it reads the evidence.

### Evaluator briefing

> A first-time, non-technical user walked the Houses app (a family
> house-hunting tool) while thinking aloud and taking screenshots. Their
> goal: find a house and get accurate commute and monthly-spend numbers.
> You are evaluating the recorded experience. Read the transcript and the
> screenshots in the manifest carefully. Every finding must trace to the
> participant's own words or a screenshot — quote the words, cite the
> screenshot. You may open the app only to confirm a detail you saw in the
> evidence, never to discover new findings.

### Evaluation rules

- Confusions: what the participant actually got stuck on, misread, or gave
  up on — in their words, ordered by how much it blocked their goal.
- Severity: judged against the goal (accurate commute + monthly spend), not
  against any feature list.
- Recommendations: concrete changes (copy, affordances, flows) that remove
  the confusion, each tied to the confusion(s) it fixes.
- Do not invent: if the participant never hit something, it is not a finding.

### Phase-2 output

## Confusions (the user's experience)
Numbered, by severity: what they were trying to do + their words/screenshot
evidence + why it confused them.

## UI/UX recommendations
Numbered: the change + where it goes + which confusion(s) it resolves.

---

## Running the two phases

1. Spawn the participant agent with the Phase-1 section (briefing + rules +
   output contract) and the parameters. Wait for its output.
2. Pass the phase-1 output (transcript + manifest) to the evaluator agent
   with the Phase-2 section. Never include phase-2 content in the phase-1
   prompt.
3. The evaluator's report is the run result. Re-run after UX changes to
   confirm the confusions are gone (P13).
