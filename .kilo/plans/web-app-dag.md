# Web App — Property Dashboard

## Strategy: UI first, migrate underneath

The sheet already has all the data. The card UI can be built **today** reading from the sheet, delivering immediate value (glanceable, mobile-friendly, grouped). Then migrate enrichment modules from sheet → SQLite DAG one at a time, leaving the sheet in place until migration is complete.

```
Slice 1:       Card UI ─── reads from ──── Google Sheet
                                             │
Slice 2:       Card UI ─── reads from ──┬─── Google Sheet
                                         │
                                         └─── SQLite DAG (1 module)
                                             │
Slice 3-N:     Card UI ─── reads from ──┬─── Google Sheet (remaining)
                                         │
                                         └─── SQLite DAG (more modules)
                                             │
Final:         Card UI ─── reads from ──── SQLite DAG (all modules)
               
               New modules go directly on SQLite DAG — no sheet column needed
```

---

## Slices

### Slice 1 — Card UI backed by the sheet (1-2 weeks)

Build a server-rendered (or simple SPA) web app that reads property data from the existing Google Sheet via `houses/sheets/reader.py` (already works, used by `GET /properties` today).

Pages:
- **Property list** — scrollable summary cards, each showing: price, bedrooms, EPC, commute summary, status. Mobile-responsive.
- **Property detail** — full card with 5 zones (Key Info, Commute & Area, Schools, Affordability, User Inputs). Colour indicators for EPC, commute times, Ofsted ratings. Expandable sections.
- **Map link** for each property (already in the sheet)

The sheet stays the source of truth. The UI is read-only (for now). This delivers immediate value: glanceable, mobile-friendly, grouped information that's hard to extract from 40 spreadsheet columns on a phone.

**Work:**
- UX design (1-2 sessions, HTML mockups)
- Frontend (1-2 weeks depending on stack choice)
- No backend changes needed — the existing FastAPI already exposes property data

---

### Slice 2 — Port one module to DAG + SQLite (1 week)

Choose one enrichment module to port from sheet formulas to the DAG node model + SQLite. Good first candidate: **EPC** (self-contained, has the richest Tier 1 data waiting to be added: floor area, age band, heating fuel).

**Changes:**
- Add core DAG types: `NodeDef`, `NodeResult`
- Define EPC-related nodes (rating, floor area, age band, heating fuel, heating cost)
- Port any EPC-related sheet formulas to Python compute functions
- Wire enrichment to write EPC node results to SQLite
- UI reads EPC fields from SQLite instead of sheet (fallback to sheet if SQLite missing)

**Dual-write:** enrichment still writes to the sheet (unchanged). It also writes to SQLite. The UI prefers SQLite, falls back to sheet. Zero risk to existing sheet data.

**User-visible:** EPC fields now have provenance. `?node=epc_rating` shows what the API returned. But the main benefit is that **new EPC fields** (floor area, age band, heating fuel) appear in the UI without needing sheet columns.

---

### Slice 3 — Provenance debug endpoint (0.5 week)

`GET /properties/{rid}/graph?node=X&depth=N` for the ported module. Agent can debug EPC API failures. UI can show a "why this value?" drill-down.

---

### Slice 4 — Port commute + schools (1-2 weeks)

Port the commute and schools enrichment to the DAG. This is the most valuable provenance target (API fallbacks for TfL, NR fares) and the most data (Simon/Lorena/Bracknell times/costs/routes, primary/secondary schools + Ofsted + distances + walks).

---

### Slice 5 — Add a new module directly to DAG (1 week)

Add crime + air quality, or planning API, or floor area ex EPC — whatever's next on the Houses tier list. Goes directly into the DAG/SQLite path. No sheet column, no formula sync, no view migration. This is the first time the DAG pays for itself: you add a module without touching a single sheet file.

---

### Slice 6 — Port remaining modules

Port walkability, town description, council tax, geo, remaining formulas (stamp duty, mortgage, etc.). Sheet becomes a fallback for fewer and fewer fields.

---

### Slice 7 (optional) — Sheet decommission

If every field is in SQLite and the web UI handles manual edits, the sheet becomes a legacy archive. This is rarely worth the ceremony — the sheet stays as a readable backup.

---

## Why this order

1. **Card UI first** = immediate user value. Mobile access, glanceability, no backend changes.
2. **Port one module** = proves the DAG works end-to-end with minimal risk (dual-write, fallback to sheet).
3. **New module on DAG** = the payoff. Adding a feature without the 7-file ceremony for the first time.
4. **Port rest** = gradually reduce sheet dependency. Each slice is independent, you can stop at any point.

The sheet never breaks. The UI never regresses. Every slice delivers something visible.
