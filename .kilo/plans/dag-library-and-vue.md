# Plan: Reactive DAG with Person-Centric Commute Model

## What & Why

The current codebase has a functional DAG engine (`houses/model/`) that stores
versioned source values and resolves derived nodes — but it's tightly coupled
to the property domain, uses flat scalar nodes, and the UI is server-rendered
Jinja2 templates with a monolithic `CardData` view model that duplicates every
field.

This plan replaces the DAG with a **generic, reusable library** (`dag/`) and
rebuilds the frontend as a **Vue app** that consumes a REST API returning
structured domain objects. The key changes:

1. **DAG becomes a generic library.** SourceNodes, ComputedNodes, and the
   persistence layer are extracted into a standalone `dag/` package at the
   project root with zero domain imports. The current `houses/model/` (DAG
   engine) is replaced by `dag/`. The new `houses/model/` contains only
   domain classes — `Property`, `Commute`, `Person`, `School`, etc.

2. **Domain objects are the API contract.** Instead of a flat `CardData` with 40
   underscore-named fields, the API returns structured `Property` objects
   containing typed sub-objects: `Commute`, `School`, `RightmoveProperty`, etc.
   The Vue frontend renders these objects directly — one component per domain
   type.

3. **Reactive signals, not lazy evaluation.** When enrichment pushes a new value
   to a SourceNode, a `changed` signal propagates through the DAG, triggering
   re-computation of downstream nodes. A WebSocket pushes the updated Property
   to the Vue client. No polling, no request-time resolution.

4. **Person-centric commute model.** `Person` is a rich class with named
   `PlaceOfInterest` entries (offices, gyms, etc.). Commutes are generated per
   `Person × PlaceOfInterest`. Adding a new POI creates an additional commute
   for every property. Updating or removing a POI triggers re-computation —
   the POI is a DAG dependency for existing commutes.

5. **Settings are SourceNodes.** Commute thresholds, person details, bus walk
   penalties — everything configurable lives in the DAG. Changing a setting
   propagates reactively through the graph.

6. **pint for units, Money for currency.** Raw numbers get proper unit wrappers.
   `money.Money` encodes currency, so field names don't need `_gbp` suffixes.

7. **Vue replaces Jinja2 completely, retaining the exact current design.**
   The current list page and detail page HTML, CSS, and screenshots are archived
   at `docs/current-ui/`. The Vue app must replicate every visual element,
   layout, and interaction — same header styles, same card layout, same commute
   pills with their colour ranges, same location map, same satellite toggle.
   The only change is the technology stack, not the pixel look.

   Reference files for the agent:

   | File | Contents |
   |------|----------|
   | `docs/current-ui/list-page.html` | List page rendered HTML (123 KB) |
   | `docs/current-ui/list-page.png` | List page screenshot (mobile viewport) |
   | `docs/current-ui/detail-page.html` | Detail page rendered HTML |
   | `docs/current-ui/detail-page.png` | Detail page screenshot (mobile viewport) |
   | `docs/current-ui/app.css` | Application stylesheet |
   | `docs/current-ui/detail.css` | Detail page stylesheet |

8. **Layer dependencies enforced.** ArchUnitPython tests ensure the `dag/`
   library never imports `houses.*`, domain objects never import `dag/` or
   `web/`, and only the application layer bridges them.

## Person (Class, Not Enum)

```python
@dataclass
class Person:
    name: str                        # "Simon", "Lorena"
    has_car: bool
    deposit_equity: Money | None     # how much deposit/equity they have
    places_of_interest: list[PlaceOfInterest]

@dataclass
class PlaceOfInterest:
    label: str                       # "Office", "Bracknell", "Dad"
    postcode: str                    # "SW1V 2QQ", "RG12 8YA", "OX7 5GZ"
```

Simon has three POIs: Office, Bracknell (a relative), and Dad. Each generates
a separate `Commute` object on every property. Adding a new POI to Simon's
settings creates a new commute on all properties. Removing one removes the
corresponding commute. Updating the postcode triggers re-computation.

Commutes are generated per Person × PlaceOfInterest:

```
Simon/Office    → Commute(destination=POI, duration=..., ...)
Simon/Bracknell → Commute(destination=POI, duration=..., ...)
Simon/Dad       → Commute(destination=POI, duration=..., ...)
Lorena/Office   → Commute(destination=POI, duration=..., ...)
```

## Commute (No Work Class, Has Person + Label)

```python
@dataclass
class Commute:
    person: Person
    label: str                         # "Office", "Bracknell", "Dad"
    destination: PlaceOfInterest       # the full POI object, not copied fields
    duration: Quantity                 # pint Quantity with units (minute)
    daily_cost: Money                  # Money encodes currency (GBP)
    details: tuple[CostGroup, ...]     # contains legs AND costs — look here for both
```

No units in variable names — `duration` not `duration_minutes`. Pint handles
the unit internally. The JSON API serialises Quantities as `{"value": 32,
"unit": "minute"}` so the client knows the unit without convention.

```python
# JSON representation
{
    "duration": {"value": 32, "unit": "minute"},
    "daily_cost": {"amount": 4.50, "currency": "GBP"},
    ...
}
```

`details` replaces the old `cost_groups` name. The `CostGroup` class itself is
unchanged.

### Everything Derived Must Be a ComputedNode

The `Commute` dataclass above is the output of a ComputedNode, not a bag of
manually-set fields. `duration`, `daily_cost`, and `details` are all
produced by the ComputedNode's compute function — they are not assigned
ad-hoc elsewhere:

```python
class SimonOfficeCommuteNode(ComputedNode[Commute, [GeoPoint, PlaceOfInterest]]):
    deps = (best_location, simon_office_poi, simon_office_transit, simon_office_bus)

    def compute(self, origin, poi, transit, bus) -> Attempt[Commute]:
        if not origin.is_succeeded or not poi.is_succeeded:
            return self._impossible({"origin": origin, "poi": poi})
        if transit.is_succeeded and self._walk_ok(transit.value()):
            best = transit.value()
        elif bus.is_succeeded:
            best = self._merge(transit, bus)
        else:
            return self._impossible({"transit": transit, "bus": bus},
                                    extra="walk too long and no bus")
        return Attempt.succeeded(
            Commute(
                person=Person.SIMON,
                label=poi.value().label,
                destination=poi.value(),
                duration=best.duration,
                daily_cost=best.total_cost,
                details=best.details,
            ),
            Provenance("TfL + Bus",
                       source_attempts={"transit": transit, "bus": bus}),
        )
```

The rule: **every value that depends on a SourceNode must be produced by a
ComputedNode.** No ad-hoc property calculation outside the DAG. If you find
yourself computing `duration` from legs in a signal handler or a dataclass
property, that calculation belongs in a ComputedNode instead.

### Exception: UI-Only Computations

Computations that exist solely to format data for the website do NOT need
a ComputedNode. These stay in the Vue frontend:

- **Colours** — mapping commute duration to `good`/`warn`/`bad` using the
  person's threshold settings. The API returns the raw `duration: Quantity`
  and the person's `commute_thresholds`. Vue computes the colour client-side.
- **Text descriptions** — composing a human-readable summary from legs
  ("Tube to Victoria, then walk 5 min"). The API returns `details: list[CostGroup]`
  with all legs. Vue formats the description.
- **Duration formatting** — "32 min" vs "1h 15m". The API returns the raw
  `duration: Quantity`. Vue formats the display string.

This keeps the DAG free of presentational logic while the Vue components
handle all view concerns — matching the current Jinja2 template approach
where `commute_colour()` and `_dur()` are helper functions in the view layer.

## Person Settings as SourceNodes

Settings are stored as DAG SourceNodes. Changing a setting propagates
through the graph and triggers re-computation.

```
GET /settings
→ 200 {
    persons: [
      {
        name: "Simon",
        has_car: true,
        deposit_equity: 50000,
        places_of_interest: [
          { label: "Office", postcode: "SW1V 2QQ" },
          { label: "Mum's house", postcode: "RG12 8YA" }
        ]
      },
      {
        name: "Lorena",
        has_car: false,
        deposit_equity: 30000,
        places_of_interest: [
          { label: "Office", postcode: "EC3A 7LP" }
        ]
      }
    ],
    commute_thresholds: {
      "Simon": { good_max_minutes: 30, fine_max_minutes: 45 },
      "Lorena": { good_max_minutes: 40, fine_max_minutes: 60 }
    },
    bus_walk_penalty_minutes: 10,
    ...
  }

PATCH /settings/persons/simon/places_of_interest
→ body: { label: "Gym", postcode: "EC1A 1BB" }
→ pushes to simon_places_of_interest SourceNode
→ signals propagate → new Commute calculated for all properties
```

## Property Domain Object

```python
class Property:
    """A property's assembled data. Every field is a ComputedNode or SourceNode.

    The nodes produce Attempt[T] when called. The API reads from these nodes
    to serialise the response. Changes propagate reactively via signals.
    """

    rid: str
    best_address: BestAddressNode                # → Attempt[str]
    best_location: BestLocationNode              # → Attempt[GeoPoint]
    rightmove_address: SourceNode[str]
    rightmove_bedrooms: SourceNode[int]
    rightmove_price: SourceNode[float]
    rightmove_location: SourceNode[GeoPoint]
    commute_nodes: list[ComputedNode[Commute]]   # one per Person × POI
    schools: ComputedNode[Schools]
    council_tax: ComputedNode[CouncilTaxInfo]
    epc: ComputedNode[EpcRating]
    walkability: ComputedNode[Walkability]

    def __init__(self, rid: str, ...):
        # wire signals from all nodes → self._changed.emit()
        ...
```

Every piece of data is a node. The Property class holds references to its
constituent nodes and wires their signals so that when any source changes,
the change propagates to the WebSocket and Vue re-renders.

Every sub-object is `Attempt[T]`. The Vue frontend renders each section
based on the Attempt state:

```
PropertyDetail.vue
  ├── LocationMap.vue           ← property.best_location.attempt() → Attempt[GeoPoint]
  ├── CommuteList.vue           ← property.commute_nodes[n].attempt() → Attempt[Commute]
  │     └── CommutePill.vue     ← individual Attempt[Commute]
  ├── SchoolsSection.vue        ← property.schools.attempt() → Attempt[Schools]
  └── InfoSection.vue           ← council_tax, epc, walkability
```

The API endpoint calls `.to_json()` on each node and includes the result in
the response. Nodes serialise themselves — the API never touches `attempt()`,
`value_type`, or any other internal:

```python
# Node base class provides .to_json() → dict
class Node[T]:
    def to_json(self) -> dict:
        attempt = self.attempt()
        return {
            "succeeded": attempt.is_succeeded,
            "value": TypeAdapter(self.value_type).dump_python(attempt.value_or_none())
                     if attempt.is_succeeded else None,
            "error": attempt._error if not attempt.is_succeeded else None,
            "provenance": self._provenance_to_json(attempt.provenance),
        }

# API endpoint:
@app.get("/properties/{rid}")
async def get_property(rid: str):
    prop = property_registry.get(rid)   # Property object with node references
    return {
        "rid": rid,
        "location": prop.best_location.to_json(),
        "commutes": [n.to_json() for n in prop.commute_nodes],
        "schools": prop.schools.to_json(),
        "council_tax": prop.council_tax.to_json(),
        "epc": prop.epc.to_json(),
        "walkability": prop.walkability.to_json(),
    }
```

The API endpoint merely composes node outputs into the response shape.
Nodes own their serialisation.

## Location Resolution (Dependency of All Commutes)

Every commute starts from the property's address. The DAG resolves the best
available location through a priority chain that already exists and works
correctly in the current codebase (`houses/model/property.py:best_location`):

```
precise_location  (Attempt[GeoPoint])   — user-set via map picker or imported
  │                                       from sheet Actual Latitude/Longitude.
  │                                       Stored as a user_input node.
  │ priority
geocode(best_address)                   — only if address starts with a number AND
  │                                       ends with a full postcode (single property)
rightmove_location  (Attempt[GeoPoint]) — from Rightmove map data
  │
best_location  (ComputedNode[GeoPoint])
```

The `corrected_address` node (also a user_input) is set when the user edits
the address via the detail page or when the sheet import upgrades the address
with the full postcode from the sheet's Postcode column. The `best_address`
ComputedNode selects between `corrected_address` and `rightmove_address`.

All commute ComputedNodes depend on `best_location` as their origin. When
the user corrects the location via the map picker, every commute re-computes.

```python
class BestLocationNode(ComputedNode[GeoPoint, [GeoPoint, GeoPoint, str]]):
    deps = (precise_location, rightmove_location, best_address)

    def compute(self, precise, rightmove, address):
        if precise.is_succeeded:
            return precise
        if _is_single_property_address(address.value_or_none()):
            return geocode_node.attempt()   # geocode is itself a ComputedNode
        if rightmove.is_succeeded:
            return rightmove
        return self._impossible(
            {"precise_location": precise, "rightmove_location": rightmove},
            extra=f"address '{address.value_or_none()}' not single-property",
        )
```

The `best_address` node (which selects between user-corrected address and
Rightmove address) follows the same pattern from the current codebase.

## Commute Generation (Dependency Chain)

Every commute ComputedNode depends on `best_location` for its origin and a
`PlaceOfInterest` for its destination:

```
Property.best_location  ─────────────────────┐
Person.Simon.places_of_interest["Office"] ──┐│
                                            ▼▼
                              simon_office_commute (ComputedNode)
                              ├── transit_route (TfL API)
                              │     └── depends on origin + destination
                              └── bus_fallback (Google Routes + BODS)
                                    └── depends on origin + destination
                                    └── replaces walk leg if too long
                                              │
                                              ▼
                                     Attempt[Commute]
```

When the map picker updates `precise_location`:
- `precise_location` SourceNode pushes new GeoPoint
- `best_location` ComputedNode re-computes
- All commute ComputedNodes receive the new origin
- All commutes re-compute
- Property emits `changed` → WebSocket → Vue re-renders all commute pills

## Legacy Compatibility

The new DAG-based enrichment runs alongside the existing sheet-based enrichment:

```
Old path (unchanged): enrichment_runner → write to sheet → Jinja2 renders
New path (alongside): enrichment_runner → SourceNode.push() → DAG → Vue renders
```

Both paths run from the same enrichment modules. The old code is not modified
until the new path fully replaces it. The SourceNode.push() is added as a
side-effect alongside the existing sheet write:

```python
# Inside enrichment_runner (temporary — both paths)
# Old path:
write_enriched_row(rid, data)    # unchanged

# New path (added):
rightmove_address_node.push(data["Address"], Provenance("Rightmove"))
rightmove_price_node.push(data["Price"], Provenance("Rightmove"))
commute_tfl_node.push(simon_commute, Provenance("TfL API"))
```

No enrichment module logic changes — just an additional push() call per output.
When the sheet is fully obsoleted, the write_enriched_row calls are removed.

## Migration Order

The order is designed so that each phase produces testable output before the
next begins. User-facing functionality is testable as early as phase 4.

### Phase 1: Foundations (testable: unit tests only)

1. **Archive current UI** — done (see `docs/current-ui/`). All subsequent
   agents reference these files to replicate the design exactly.

2. **Create `dag/` library at project root** — Signal/Slot, `Node[T]` base
   class with `_impossible()` error composition, `SourceNode[T]`,
   `ComputedNode[T]`, generic persistence with TypeAdapter auto-serialise
   and `.to_json()` on every node. Zero domain imports — testable with
   pure unit tests.

3. **Add `Attempt` with `Provenance`** — absorb existing `houses/attempt.py`
   into the dag library or keep alongside. Add `provenance: Provenance` field.
   Test: Attempt round-trips with provenance chains.

4. **Define domain classes in `houses/model/`** — `Person`, `PlaceOfInterest`,
   `Commute` (with `duration: Quantity`, `daily_cost: Money`, `details:
   tuple[CostGroup, ...]`), `CostGroup`, `JourneyLeg`, `RightmoveProperty`,
   `Property`, `School`, `Schools`, `CouncilTaxInfo`, `EpcRating`,
   `Walkability`. Pure dataclasses, no DAG logic. Use pint for quantities
   (no units in field names). Use `money.Money` for costs (currency in the
   object, not in the variable name). Test: construction and field access.

### Phase 2: Core DAG Nodes (testable: resolve_property returns correct values)

5. **Sheet import bootstrap** — reads only **user-owned columns** (Address,
   Postcode, Rightmove URL, Actual Latitude, Actual Longitude — the sheet's
   columns A–G) and pushes each field to the appropriate SourceNode.

   Calculated columns (commute times, school data, council tax, Best Latitude/
   Longitude, Approx Latitude/Longitude, etc.) are **not imported** — they
   are produced by the DAG's own ComputedNodes using the same enrichment
   modules that currently write to the sheet.

   All ComputedNodes use the existing disk-based API cache
   (`houses/api_cache.py`), which stores HTTP response bodies keyed by
   URL+params. Re-computation does not re-hit external APIs — it reads
   from the cache. This means:

   ```
   Sheet import → pushes user columns (Address, Postcode, Actual Lat/Lng)
     ↓
   DAG ComputedNodes run:
     ├── best_address        (corrected_address vs rightmove_address)
     ├── best_location       (precise_location > geocode > rightmove_location)
     ├── commute_tfl         (reads from api_cache, no new HTTP call)
     ├── commute_bus_alt     (reads from api_cache)
     └── ...
     ↓
   Property holds node references; API calls .to_json() on each
   ```

   Test: after bootstrap, `best_location_node.to_json()` returns a
   GeoPoint with provenance.

6. **Location resolution nodes** — SourceNodes for `rightmove_address`,
   `corrected_address` (user_input), `rightmove_location`,
   `precise_location` (user_input). ComputedNodes for `best_address`
   and `best_location` with the priority chain:

   ```
   precise_location              → ComputedNode[GeoPoint]  (user-set)
   geocode_node                  → ComputedNode[GeoPoint]
     └── depends on best_address  (only when address is a single property)
   rightmove_location            → ComputedNode[GeoPoint]  (Rightmove map)
     │
   BestLocationNode              → ComputedNode[GeoPoint]
     └── priority: precise > geocode > rightmove
     └── on failure: calls _impossible() with dep errors
   ```

   Test: resolving best_location for a property returns the expected GeoPoint
   depending on which source is available. Assert error messages are
   specific when a source fails.

7. **Settings SourceNodes** — GET/PATCH /settings endpoint. Settings include
   person definitions (name, has_car, deposit_equity, places_of_interest),
   commute thresholds, bus_walk_penalty_minutes, etc. Settings are SourceNodes
   so changes propagate reactively. Adding a POI creates a new Commute
   ComputedNode; removing one unregisters it. Test: patch a setting, verify
   downstream ComputedNodes re-compute.

### Phase 3: Commute Pipeline (testable: Commute objects produced correctly)

8. **Commute ComputedNodes** — each Person × PlaceOfInterest gets four
   ComputedNodes:

   ```
   simon_office_transit_node  → ComputedNode[Commute]  (TfL API, depends on
   │                                                     best_location + POI)
   simon_office_bus_node      → ComputedNode[Commute]  (Google Routes + BODS,
   │                                                     depends on best_location + POI)
   simon_office_commute_node  → ComputedNode[Commute]  (selector, depends on
                                                         transit + bus nodes)
   ```

   All runtime errors use `_impossible()` from the base Node class so
   error messages include specific failure details from each dep.

   Test: given a best_location and a POI, commute_node.to_json() returns
   a Commute with expected `details`, `duration`, `daily_cost`.

9. **Property signal wiring** — Property holds references to its constituent
   ComputedNodes. It wires their `changed` signals so that when any commute
   updates, Property emits its own `changed`. Test: pushing a new value
   to `precise_location` triggers Property.changed with updated commutes.

### Phase 4: REST API (testable: curl returns expected JSON)

10. **Property REST endpoint** — `GET /properties` returns JSON composed from
    each node's `.to_json()`, `GET /properties/{rid}` returns single property
    JSON. The API never accesses node internals (`.attempt()`, `.value_type`)
    — it only calls `.to_json()`. Add `PATCH` endpoints for user edits
    (address, location) which push to SourceNodes.

    ```python
    @app.get("/properties/{rid}")
    async def get_property(rid: str):
        prop = property_registry.get(rid)
        return {
            "location": prop.best_location.to_json(),
            "commutes": [n.to_json() for n in prop.commute_nodes],
            "schools": prop.schools.to_json(),
            ...
        }
    ```

    Test: `curl /properties/12345 | python3 -m json.tool` verifies structure.

11. **Settings REST endpoint** — `GET /settings`, `PATCH /settings`. Test:
    curl settings endpoint, verify person list, commute thresholds, etc.

### Phase 5: WebSocket + Vue Frontend (testable: headless Chrome DOM comparison)

12. **WebSocket endpoint** — emits `Property.changed` events (node `.to_json()`
    payloads) to connected Vue clients. Test: connect via Python websocket
    client, push a source value, verify update message arrives.

13. **Vue 3 application scaffold** — agent must first research Vue 3 best
    practices (see Research Requirement below). Build scaffold with Vite,
    fetch properties from REST API, render list page. Compare DOM against
    `docs/current-ui/list-page.html` using `--dump-dom` after each
    significant component. Validate commute pills, school cards, map
    element, provenance badges all match the archived reference.

14. **Detail page components** — PropertyDetail, LocationMap, CommutePill,
    SchoolsSection. Each component receives `to_json()` output (the
    Attempt-with-provenance structure) and renders based on the attempt
    state. Compare DOM against `docs/current-ui/detail-page.html`.

### Phase 6: Cutover (testable: full parity with old UI)

15. **Add SourceNode.push() to enrichment modules** — alongside existing
    `write_enriched_row()` calls. No logic changes.
    Test: run enrichment, verify SourceNodes have new values via `.to_json()`.

16. **Dual-path operation** — old Jinja2 templates and new Vue app run
    side by side. Compare outputs for parity.

17. **Remove old `houses/model/` DAG engine** — the generic `dag/` library
    fully replaces it. Migrate any remaining domain-specific node code.

18. **Remove display-only old code** — at this stage only the Jinja2 templates
    and `card_data.py` can be removed. The old enrichment modules, sheet
    writes, and `houses/sheets/` package must remain untouched — the
    spreadsheet is still the primary enrichment target and will be deprecated
    in a later plan. Removing old code that would break spreadsheet
    enrichment is out of scope for this plan.

## Research Requirement Before Vue

Before writing any Vue code, the agent must research and document:

- Vue 3 Composition API conventions (script setup, composables, provide/inject)
- State management patterns (Pinia stores vs reactive composables)
- Component hierarchy design — how `PropertyDetail`, `CommutePill`,
  `LocationMap`, etc. communicate with each other
- WebSocket integration in Vue (how to subscribe to Property.changed and
  update the store)
- How to structure a Vue project alongside an existing Python server (Vite
  proxy config for API calls)
- Testing Vue components (Vitest, @vue/test-utils)

The research should be written up as a brief decision record
(`docs/vue-architecture-decisions.md`) covering the key choices made
and why, so future agents understand the reasoning.

## Implementation Process

The agent must follow these rules throughout:

1. **One step at a time.** Complete each migration step fully before starting
   the next. Do not write code for step 4 while step 3 still has failing tests.

2. **Tests first, code after.** Every new module, function, or class must have
   a failing test before its implementation. Follow the existing coding
   standards at `docs/coding-standards.md` — test behaviour, not mock call
   patterns; use fakes for network/sheet I/O; validate test data against
   canonical sources.

3. **All tests must pass before advancing.** After each migration step, run
   the full test suite. A step is complete only when `pytest tests/unit/ -q`
   reports zero failures.

4. **UI verification with headless Chrome.** When building the Vue frontend
   (step 10), regularly compare the rendered DOM against the archived
   reference files in `docs/current-ui/`:

   ```bash
   chromium --headless --no-sandbox --disable-gpu --dump-dom 'http://localhost:8080' > /tmp/current.html
   diff <(xmllint --format /tmp/current.html) <(xmllint --format docs/current-ui/list-page.html)
   ```

   Check that:
    - All cards render with the same structure (header, address, pills, score)
    - Commute pills have the correct label, duration, and colour classes
    - Map element is present with correct data attributes
    - Provenance badges match the expected text

    The agent cannot view images — use `--dump-dom` for DOM structure
    comparison and `grep` for content checks against the reference HTML.

   Run this check after every significant Vue component is built.

5. **Do not skip steps.** The ordering is deliberate — foundation (dag library)
   before domain objects, domain objects before signals, signals before API,
   API before Vue. Jumping ahead will produce code that can't be tested in
   isolation.
