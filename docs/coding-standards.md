# Coding Standards — Houses

Project-specific rules supplementing the shared coding standards. Read both. If they conflict, this file takes precedence.

## Design Principles

| Principle | Rule |
|---|---|
| **Separation of concerns** | One reason to change per module/class/function. HTTP vs business vs persistence live in different modules with one-way dependency chains. Urge to import from a sibling layer or mix I/O with computation = split, not shortcut. |
| **Cohesive classes** | Data + behaviour together. A class owns its invariants; external code never reaches into a dataclass to compute derived values. Public-field classes that others manipulate = poorly-organised dict. `get_*` accessors feeding procedural code = missed abstraction. |
| **Names communicate intent** | Domain names, not shapes: `monthly_mortgage_payment` not `calculate_value_3`. A name needing a comment = failed name. Classes = domain nouns (`StampDutyNode`); functions = verbs (`resolve_property`); variables = what they hold (`price` not `x`); booleans read in `if` (`has_school` not `school_flag`). |
| **Anti-fragile: correct by construction** | Types make invalid states unrepresentable; pure functions preferred; error paths explicit in the type system (discriminated unions, not `None`); never cast/suppress type-checker flags; happy path reads naturally. Signs of coincidental correctness: works only in your test env, reordering "happens to work", unrelated breakage, unwritten rules ("always call X before Y"). |

## Value Types

### Semantic types over primitives

| Primitive | Semantic type | Why |
|-----------|-------------|-----|
| `str` for a point in time | `datetime.datetime` | Timezone-aware arithmetic, no parsing errors |
| `float` for a price | `money.Money` | Currency part of the value; no silent £/$ mix-ups |
| `int` for a duration | `pint.Quantity` | Unit part of the value; metres ≠ kilometres |
| `dict` for structured data | `dataclass`/`TypedDict` | Field names, types, required/optional explicit |
| `str` for enumerated value | `enum` | Valid values known at compile time |

**All monetary values use `money.Money`; all durations/distances use `pint.Quantity`** — never bare `float`/`int`. `Money`/`Quantity` encapsulate the unit, so field names don't repeat it (`price`, not `price_gbp`).

**Always turn numeric literals into quantities by multiplying by a unit constant** — never by calling the `Quantity` constructor with a literal unit string:

```python
# ✗ constructor with a literal unit
Quantity(4.0, "km")

# ✓ one-unit Quantity constant, multiplied
KM = 1.0 * ureg.km          # one kilometre as a Quantity
radius = 4.0 * KM           # Quantity(4.0, km)
```

The constants are **one unit expressed as a Quantity** (`1.0 * ureg.km`), not a bare `Unit`: the pint stubs type `scalar * Unit` as an unhelpful `Unit | Quantity` union, while `scalar * Quantity` is cleanly a `Quantity` — and `4.0 * KM` is 4 km either way. Define the constants once per package from a single `UnitRegistry` (`tools/commute/units.py` for the toolchain; pint forbids mixing quantities from different registries). Keep the `Quantity` constructor only for parsing string/unknown input (e.g. config values).

Before reaching for a `dict`/list/primitive: "Is there a type that makes this impossible to misuse?"

**Wire formats are the exception, explicitly:** serialized payloads, config files, and external-API request bodies use unit-named bare numbers by design (`threshold_min`, `cell_km`, `duration_min`) — pint has no JSON representation, and the consumers (scrapers, URLs, the map) read numbers. `Quantity` governs all in-memory computation and function signatures; conversion happens only at the (de)serialization boundary. The batch toolchain (`tools/commute/`) follows this: `Quantity` signatures internally, bare unit-named numbers in `data/commute/*.json` and the config file.

### Each class in its own module

Named after the class. Exception: a module grouping closely related small dataclasses (e.g. `models.py` with several handful-of-fields, no-behaviour models sharing one reason to change). Extract once a class grows non-trivial behaviour.

## DAG Model Is the Source of Truth

**`houses/nodes/` is the single authoritative store for all resolved property data** — address, location, bedrooms, price, commutes, schools, council tax, EPC, walkability. The dependency flow is Input → source_values → resolver → derived_values → Output (see `docs/architecture.md` for the layer diagram).

### What belongs in the DAG

Every enrichment module producing a property value pushes it into a source node. Every display reads derived values. **No module re-implements a priority chain or combines raw inputs — the DAG resolver does that once.**

| Code | Does | Never does |
|---|---|---|
| Bootstrap/import | pushes source nodes via `bootstrap_from_row()` / `UserInputNode.push()` | re-implements priority/validation |
| Card/display | reads resolved values via the property registry / node JSON | decides which value is "best" |
| Enrichment runners | push source values, drain the scheduler | make priority decisions |

### Design for new nodes

1. Declare **source nodes** in `houses/nodes/` for each raw input.
2. Declare **derived nodes** for resolved values.
3. **Enrichment module** pushes raw data into the source node.
4. **Templates/API responses** read derived values via `PropertyNodes.to_json()` / the property registry.

Staleness, re-computation, priority are the DAG's job. See `docs/dag-library.md` for node patterns.

### Rule of thumb

A business rule needed in two places (e.g. "user correction overrides Rightmove") belongs in a DAG node definition — not in both the import function and the card builder. Resolve once; everything else reads.

## Houses-Specific Practices

### Datetimes: UTC, aware, explicit boundaries

1. **Store/process UTC.** Never `datetime.now()` — always `datetime.now(UTC)`.
2. **Display local** at the presentation boundary (template, API response). Model never stores local times.
3. **External sources**: document the source's timezone, convert explicitly to UTC before storing.
4. **From DB**: `fromisoformat` may return naive. After parsing, check `dt.tzinfo is None` → `dt.replace(tzinfo=UTC)`.

Naive datetimes are a systemic bug source: aware↔naive comparisons raise `TypeError`; arithmetic is wrong across DST.

### API keys & secrets

Environment only. `.env` is for non-secret config. **Never read, log, print, echo, or store keys** in context, files, code, output, cache keys, URLs, or request bodies — headers only. Redact keys from error messages before logging.

### Fail fast, don't pre-check

Don't pre-validate before trying — let code fail naturally. Don't pre-check API keys before the call: missing key → 403 propagates as a normal API error.

Exception: interactive/CLI setup flows may pre-check configuration when the natural failure is misleading (e.g. Google's device endpoint rejects an unconfigured client with a confusing `invalid_client`). Such pre-checks must emit the two-tier messages below.

### No backward compatibility shims

**Delete dead code, don't deprecate it.** A shim compiles, passes tests, lulls readers into thinking it's real, and never gets cleaned up. Rename/remove + update every caller in the same commit. No aliases, no re-exports, no "will remove in a future version".

### Never swallow errors

Every `except` block must log, re-raise, or handle observably. Bare `except: pass` / silent `except Exception:` forbidden. Safe-to-ignore errors log at `DEBUG` with an explanation.

```python
# ✗ invisible
try:
    do_something()
except Exception:
    pass
# ✓ observable
try:
    do_something()
except Exception as e:
    logger.debug("do_something failed (non-fatal): %s", e)
```

DAG-specific error rules (`AttemptError` contract, API services return Attempt vs pure code throw, transient re-raise/retry, nodes propagate never re-literalize) → [dag-library.md](dag-library.md) *The three-state result: `Attempt[T]`*.

### Two-tier failure messages

Every fail-fast path that can be triggered by environment, configuration, or user error emits **two** messages:

1. **User message** — what happened + what to do next, in product language. No internal identifiers: env var names, config keys, class/function names, endpoints, status codes, response bodies, stack traces, or secrets. Commands the user runs (e.g. `make run`) are fine — they're the fix.
2. **Dev log** — root cause + exact resolution, enough to fix without re-running with debug flags: the env var to set, the failing URL/status, the response body (secrets redacted), the exception.

| Surface | User message goes to | Dev detail goes to |
|---|---|---|
| HTTP endpoint | response `detail` (concise, stable for clients) | `logger.warning(...)` with the resolving detail |
| CLI tool | `print(..., file=sys.stderr)` | `logger.warning(...)` — stderr is the tool's diagnostic channel |

```python
# ✗ one dev-only message: no plain-language line, nothing in the log
sys.exit("No Google OAuth device client configured — set HOUSES_GOOGLE_DEVICE_CLIENT_ID")

# ✓ user line + resolving log line
print("Google sign-in isn't set up on this machine — run the sign-in again once it's configured.", file=sys.stderr)
logger.warning("HOUSES_GOOGLE_DEVICE_CLIENT_ID unset — create a 'TVs and Limited Input devices' OAuth client in Google Cloud Console and add its ID to .env")
sys.exit(1)
```

CLI tools implement this as one helper (e.g. `_fail(user_message, dev_detail)` in `tools/capture_dom.py`) so no path can forget one half.

### Cache key hygiene

- Never include API keys in cache key parameters (rotation shouldn't invalidate the cache).
- Never cache non-OK API responses (`REQUEST_DENIED`) — a temporary key issue must not poison the cache.

### Force parameter discipline

- `force=true`: overwrite existing cells. Only when new data is known better.
- `force=false` (default): fill blank cells only. Safe default for incremental enrichment.
- `force` must reach BOTH `_batch_stream()` and `_write_backfill_cells()`. If the call chain drops it, every cell is treated as "already has data".

### Querying properties

`GET /properties` and `GET /properties/{rid}` REQUIRE `?tab=view` or `?tab=data` — otherwise an error.

## Dependency Injection

Three DI patterns (implementations in `houses/services.py`, `houses/context.py`; see `docs/architecture.md` for the full DI section):

| Pattern | When |
|---------|------|
| **`Services` container** | Replace an entire enrichment module (EPC, council tax, commute) |
| **Context vars** | Per-request singletons (Rightmove scrape fn) |
| **Local `_kwarg`** | Leaf-level data objects (car park data, HTML fixtures) |

Reusable fakes in `tests/helpers.py`; `make_services()` builds a `Services` with all fakes at sensible defaults.

## Testing

### Mocking layers

| Layer | Technique | Notes |
|---|---|---|
| 1. Pure functions | no mocking | real inputs → assert outputs |
| 2. Function-param injection | `_kwarg` fake | no monkeypatch, no MockTransport |
| 3. `Services` container | fakes → `_run_enrichment` | `make_services(epc_service=FakeEPC(band="C"))` |
| 4. `ContextVar` | set per-request state for test scope | try/finally reset |
| 5. MockTransport (legacy) | patches httpx transport | migrate to DI; `tests/integration/conftest.py` |

**Never use `monkeypatch`/`unittest.mock.patch`/`MockTransport` in new tests** — they patch global state and break on refactors. If something isn't reachable through DI, refactor the code to accept a dependency. See `docs/testing-standards.md` for the full reference (naming, determinism, fakes).

### Reusable fakes

`tests/helpers.py` provides a fake for every service protocol. Every fake declares its protocol as base class (`class FakeEPC(EPCLookupService)`); `make_services` override kwargs are typed against the protocols — a protocol signature change is flagged by basedpyright at edit time, never at runtime.

### Test organization

- **Unit** (`tests/unit/`): one function/module in isolation, no API calls.
- **Integration** (`tests/integration/`): full pipeline; `Services` fakes, `ContextVar`, or MockTransport.
- **E2E** (`@pytest.mark.e2e`): real external APIs; one consolidated suite per API; skipped by default.

### Money in assertions

The [Money rule](#value-types) applies to tests too — **never float-compare
monetary values, even in test helpers.** The wire form is
`{"amount": "35500.00", "currency": "GBP"}`; parse amounts with `Decimal`.
`float` introduces representation noise that breaks exact-delta assertions
(`35500.00 − 25500.00` must equal `10000.00`, not `9999.999…`).

```python
# ✗ float — inexact; violates the Money rule
assert float(a["amount"]) - float(b["amount"]) == 10000.0

# ✓ Decimal over the wire strings — exact, unit-aware comparison
from decimal import Decimal

assert Decimal(a["amount"]) - Decimal(b["amount"]) == Decimal("10000")
```

### DAG tests are deterministic — one flush drains the whole cascade

`flush_processor()` drains the **entire** refresh cascade in one call: a
node's refresh emits synchronously, queueing its dependents inside the same
drain loop. **Never call `flush_all()` twice "for the two waves".** A test
that seems to need two flushes has a bug — investigate (wrong node read,
wrapper comparison, missing drain) instead of adding flushes. Tests are
single-threaded; there is no timing dependency to work around.

**Compare unwrapped values, never Attempt wrappers.** Two reads of the same
node produce wrapper dicts that always differ (provenance timestamps are
rebuilt per read), so `a != b` on wrappers passes even when **nothing**
propagated:

```python
# ✗ vacuous — wrapper provenance timestamps differ on every read
assert updated["affordability"]["mortgage_required"] != baseline["affordability"]["mortgage_required"]

# ✓ unwrap the Attempt value, compare the money amount (Decimal)
assert _amount_of(updated) == _amount_of(baseline) - Decimal("10000")
```

**Tests drain explicitly; production has a background processor.** The
lifespan starts a processor that refreshes scheduled nodes automatically
(and the WS broadcaster pushes the results); the test environment has none,
so a propagation test drains with `flush_all()` and says so in a comment. A
settings-change regression test must assert the **exact** money delta —
then a broken dependency wiring (e.g. a node missing the `persons` dep)
fails loudly instead of decaying into a staleness race.

## Documentation

- **Delete, don't archive.** Obsolete content is a liability. Wrong = remove; no archive dirs, no deprecation notices.
- **Single source of truth**: each fact in exactly one place; other docs link, never repeat.
- **Docs must match code**: rename a function/module/tab → update docs in the same commit.
- **Docs are anti-fragile**: don't restate what the code says (signatures, defaults, file lists). If a fact is discoverable from code, leave a reference to the code instead — see [dag-library.md](dag-library.md) for the pattern.
