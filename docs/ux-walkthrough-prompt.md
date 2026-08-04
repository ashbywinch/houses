# UX Walkthrough — Repeatable Prompt

Run this experiment by handing the whole file (or its "Task" section) to a
UX/design subagent with browser access. The agent walks the live app as a
non-technical first-time house buyer, READ-ONLY, and reports confusions +
UI/UX recommendations. Baseline for judging findings:
[docs/usability-requirements.md](usability-requirements.md).

## Parameters (update per run)

- App URL: `http://127.0.0.1:5173/` (frontend; backend `http://127.0.0.1:8765`).
  Use the LAN URL (`http://<ip>.sslip.io:5173/`) when testing phone access;
  the sandbox cannot resolve sslip.io.
- Auth: the browser may already hold a session. If a "Sign in with Google"
  page appears, set the session cookie first: read the value from
  `/tmp/cookie.txt` (or mint one) and apply it with
  `page.setCookie({name: 'session', value: '<value>', domain: '127.0.0.1', path: '/'})`,
  then reload.
- Read-only: the agent MUST NOT save, PATCH, submit, or toggle anything
  persistent. Observation and navigation only.
- Scenario set: the current scenario section below.

## Context

Family house-hunting tool ("Houses"). Hash-router SPA: main screen `#/`
(property list — ~40 cards with price, monthly cost, commute rows), property
detail `#/property/<rid>` (commute breakdown, monthly costs, provenance /
"how we got this" calculations), `#/settings` (family settings page:
per-person sections with has-a-car, money fields, commute thresholds, and a
"Commutes" list of places with address, trips/week, and mode checkboxes —
Trains/Driving/Walking). The header has a "Settings" link when logged in.
The detail page's provenance view shows how derived numbers (e.g. total
monthly cost) are computed — READABLE without saving.

## Scenarios (the "user")

The user is the family parent (Simon) — non-technical, never used this app,
wants to buy a house. Walk the app separately for EACH scenario below,
keeping confusions per scenario.

### Scenario A — moved office, planning to sell for more
1. They just MOVED OFFICE — the commute destination shown on cards is the
   OLD place (e.g. "Pimlico").
2. They plan to SELL their current home for MORE than the app assumes, so
   the proceeds are higher and the new mortgage will be SMALLER — the
   "monthly payment" figure should reflect that lower mortgage.

### Scenario B — house sold, cash in hand (NEW use case)
1. They have ALREADY SOLD their current home — there is no house, only the
   sale proceeds sitting as cash.
2. They are buying the next house as cash-rich buyers with no chain.
3. The current-home money fields ("expected sale price", "mortgage
   remaining") describe a house they no longer own; the deposit is the
   realized cash pile. Judge: can the user express "we've sold, it's all
   cash now"? Does the app stop asking about the current home? Does the
   affordability/mortgage math still make sense for a cash buyer (no
   mortgage → monthly payment = running costs only)?

## Task

1. Start at the MAIN SCREEN (`#/`) — look first as the user would: what do
   the cards promise, what jumps out, what would a confused user click?
2. For EACH scenario, explore the way the user would to make the numbers
   they care about correct (commute distance; monthly payment), including
   the settings page and a property detail page. READ the provenance to
   understand what drives the numbers — never change anything.
3. Keep a running list of EVERY point where a non-technical user would be
   confused, stalled, or misled — in their voice (e.g. "Where do I put my
   new office?", "Why does it still say Pimlico?"). Note exact UI
   text/labels, missing affordances, buried controls, jargon, numbers that
   don't visibly update, states the app cannot represent.
4. Ground every observation in what you actually saw (URL, exact label).
   If you could not find something, say so — the absence is a finding.

## Report (markdown, two sections, evidence-first)

## Confusions (the user's experience)
Numbered list, ordered by severity, each item: what the user is trying to do
+ the exact UI text/label/location + why it confuses them. Mark which
scenario (A/B) each applies to.

## UI/UX recommendations
Numbered list, each: the concrete change + where it goes + which
confusion(s) it resolves. Be concrete and terse.
