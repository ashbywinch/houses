# Plan: AI-Powered PR Code Review

## Goal

Add an automated AI code review step to the CI pipeline that reviews every PR against our coding standards, performs a security audit, checks test quality, and posts inline comments on the diff.

**Model:** `opencode/deepseek-v4-flash` via OpenCode Go (OpenAI-compatible API)

---

## Tool: PR-Agent (community-maintained Qodo open-source)

**Repo:** `github.com/the-pr-agent/pr-agent` (12k+ stars, MIT)

Chosen over alternatives (deepseek-review, Gito, custom script) for:
- **Inline comments** on specific code lines via the `/improve` tool
- **Built-in security review** (`require_security_review=true`)
- **Built-in test review** (`require_tests_review=true`)
- **Supports any OpenAI-compatible API** — drop in OpenCode Go as the provider
- **`repo_context_files`** feature — injects `docs/coding-standards.md` and `AGENTS.md` as prompt context so every review is aware of our conventions
- Mature, widely used, actively maintained

---

## What the Review Checks

| Category | How |
|---|---|
| **Common sense** (bugs, logic, performance) | Default PR-Agent review behaviour |
| **Coding standards** (DI patterns, types, architecture) | `repo_context_files` loads `docs/coding-standards.md` into every prompt |
| **Security audit** (hardcoded keys, injection, cache poisoning) | `require_security_review=true` + `extra_instructions` |
| **Test quality** (coverage, isolation, no monkeypatch) | `require_tests_review=true` + standards context |
| **Inline fix suggestions** | `/improve` tool posts line-level comments on the diff |

---

## Phases

### Phase 1: Audit & Augment `docs/coding-standards.md`

Before the review can enforce our conventions, they must be formally documented.

**Gaps to fill** (conventions used in code but missing from the standards doc):

| Convention | Used in | Not yet documented |
|---|---|---|
| `money.Money` for all monetary values — never `float` | All DAG nodes, bus_journey, car_park | ❌ — only mentioned in `model/domain.py` docstring |
| `pint.Quantity` for durations/distances — never bare int | transit.py, petrol.py, commute.py, rail_fares | ❌ |
| No monkeypatching in tests — use `_kwarg`, `Services`, `ContextVar` | tests/helpers.py, coding-standards §Testing | Mentioned in passing, needs explicit prohibition |
| `_kwarg` naming convention | `_registry`, `_page_path` throughout | Partially covered |
| Test file naming and organization | tests/unit/, tests/integration/ | Not explicitly stated |
| Security rules — keys from env only, never log | config.py, AGENTS.md | Thin section |
| Test patterns — fakes in helpers.py, `make_services()`, deterministic tests | tests/helpers.py | Partially covered |

**Follow the `write-documentation` skill:**
- Each piece of information goes in exactly one place
- Each doc covers one topic for one audience
- No duplicated content — link between docs where needed
- The review prompt loads all docs via `repo_context_files` rather than duplicating their content
- If a topic belongs in a separate doc, create it and add it to the `repo_context_files` list

**During Phase 1, decide which gap items land in which doc:**
- `docs/coding-standards.md` — DI patterns, Money/Pint types, DAG rules, `_kwarg` convention, no monkeypatch
- `docs/testing-standards.md` — test organization, fakes in helpers.py, `make_services()`, deterministic test requirements (create if it doesn't exist)
- `docs/architecture.md` — layer isolation rules (already exists, add if gaps)

### Phase 2: Create `.pr_agent.toml`

Project-root configuration file. Key settings:

```toml
[config]
model = "opencode/deepseek-v4-flash"
base_url = "${OPENCODE_GO_BASE_URL}"
repo_context_files = [
    "docs/coding-standards.md",
    "docs/architecture.md",
]
repo_context_from_default_branch = true

[pr_reviewer]
require_tests_review = true
require_security_review = true
require_estimate_effort_to_review = true
num_max_findings = 5
```

**No `extra_instructions` for coding standards.** The standards live solely in their respective doc files under `docs/`. PR-Agent's `repo_context_files` injects every listed doc into the review prompt — the model reads them directly. Separating the rules into `extra_instructions` would fork the source of truth.

`extra_instructions` is reserved for ephemeral guidance that doesn't belong in a standards doc (e.g. "focus extra attention on error handling in this sprint").

`repo_context_files` reads from the default branch only, so a PR cannot tamper with the standards it's judged against.

**When Phase 1 produces new docs** (e.g. `docs/testing-standards.md`), add them to this list. Each file covers one topic for one audience per the `write-documentation` skill. PR-Agent silently skips missing files, so the config stays valid during development — but don't list files before they exist.

### Phase 3: Create `.github/workflows/pr-agent.yml`

```yaml
name: AI Code Review

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

jobs:
  pr-review:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
      issues: write
    steps:
      - uses: actions/checkout@v4
      - uses: the-pr-agent/pr-agent@main
        env:
          OPENAI_KEY: ${{ secrets.OPENCODE_GO_KEY }}
          OPENAI_BASE_URL: ${{ secrets.OPENCODE_GO_BASE_URL }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

**Secrets needed in GitHub repo settings:**
- `OPENCODE_GO_KEY` — API key for the OpenCode Go provider
- `OPENCODE_GO_BASE_URL` — OpenCode Go API endpoint

### Phase 4: Wire into existing CI

The existing `.github/workflows/tests.yml` runs on `push` (branches). The review workflow runs on `pull_request`. They're independent — no changes needed to the existing CI.

## Single Source of Truth

`.pr_agent.toml` lists every standards doc in `repo_context_files`. Each file covers one topic (coding, testing, architecture, etc.). The model reads them all on every review.

No standards content lives outside these docs. If a convention changes, update the doc and every review immediately picks it up.

`repo_context_files` reads from the repository's default branch only. A PR cannot alter the standards it's reviewed against.

## Acceptance Criteria

- [ ] `docs/coding-standards.md` covers all conventions listed in the gap table above
- [ ] `.pr_agent.toml` exists at repo root with correct model and review configuration
- [ ] `.github/workflows/pr-agent.yml` triggers on PR events and runs review
- [ ] A test PR produces a structured review with:
  - Summary comment (`/describe`)
  - Review findings including security and test assessment (`/review`)
  - Inline code suggestions on specific lines (`/improve`)
- [ ] Findings reference our coding standards where applicable
