# Rollout Process PRD

User requirements for getting a new build of `houses.blueumbrella.net` live.
This is a requirements document, not a plan — the pipeline implementation
falls out of these requirements.

## Requirements

**R1 — The new box is approved by the user before it goes live.**
The user reviews the new box on the review surface and explicitly approves it.
Nothing goes live without that approval.

**R2 — Going live is trivial and cannot possibly break the new box.**
The go-live action is the minimum possible: point the live traffic at the
approved box. It transforms nothing, migrates nothing, re-verifies nothing and
reconfigures nothing on that box. If going live could conceivably break the
box, the rollout was done wrong — the box was not ready, and it was not the
approved box.

**R3 — The new box is data-continuous with the previous one (the cutover case).**
The data the new box serves is the data the previous live box served. The data
handoff is part of making the box ready, and is complete **before approval** —
the approved box is already the data-continuous box, so the trivial go-live
(R2) leaves it unchanged.

**R4 — EXCEPTION: restore from a good backup, only because the user says so (the recover case).**
When the live box's data must not be carried forward (it is a mess), the new
box is built from an explicitly chosen backup object, and the restored state is
the state the user approves. The exception applies only on the user's
say-so — never by default, never silently.

**R5 — Nothing ever alters the live box except users using it through the
front end.**
The rollout process performs no box-level operation on the live box other than
taking its backup. Backups must be **read-only**: they never stop the box's
app, never write anything to the box, and never mutate its state. The backup
is taken while the box serves, passively. (The only box-level writes a live
box ever sees are the user's own actions in the application.)

## What the implementation must therefore satisfy

- An approval gate sits between "reviewed" and "live", and is the only step
  there.
- The go-live step changes traffic targeting only — the approved box is
  byte-identical to what was reviewed at the moment it starts serving. The
  traffic move is external to both boxes; it is not a box-level operation.
- The data handoff (staging, restore, migration) completes before approval;
  approval covers the final state the box will serve.
- In the recover case (R4), the user names the backup object; the restore is
  part of building the box; the flip is still the trivial R2 action.
- **Nothing is installed to the live/owner box during a rollout — ever.** The
  new box gets every install; the live box gets none.
- **The only box-level operation on the live box is its read-only backup**
  (R5): no freeze, no stop, no write, no mutation — backup while serving,
  passively. The box is untouched until the flip's darken step (traffic moved,
  relay retired, app stopped; files and DB kept as-is).

## Anti-patterns (explicitly not this process)

- Approval or acceptance steps after the flip.
- A database migration after approval — the approved box is the migrated box.
- Settle-as-acceptance: polling healthy reads to "prove" the flip.
- Re-verifying, re-restoring or re-migrating the approved box at flip.
- Approving a box that will change between approval and flip.
- Installing or re-tooling the live box during a rollout.
- A backup that stops, writes to, or mutates the live box — backups are
  read-only, taken while the box serves.