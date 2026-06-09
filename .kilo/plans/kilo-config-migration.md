# Kilo Shared Config Migration

Create `~/Documents/code/kilo-config/` to hold shareable Kilo config (rules, skills) separate from project-specific config. Skills and commands auto-discovered via `KILO_CONFIG_DIR`; rules loaded via explicit `instructions` paths in each project's `kilo.jsonc`.

## Why

- Share rules and skills across projects without duplication
- Keep project-specific rules in the project where they belong
- Source-control the shared config independently
- Use skills (loaded on demand) for voluminous domain guidance like coding standards — always-loaded rules stay thin and universal

## How It Works: Three Config Layers

| Layer | Path | Scope | Loaded via |
|-------|------|-------|------------|
| **Global** | `~/.config/kilo/` | Personal preferences across all projects | Kilo auto-loads this on startup |
| **Shared** | `~/Documents/code/kilo-config/` (set via `KILO_CONFIG_DIR`) | Cross-project reusable rules/skills | `KILO_CONFIG_DIR` for skills/agents/commands; `instructions` key in global config for rules |
| **Project** | `.kilo/` in project root | Specific to this project | Scanned by Kilo on startup |

**Key concept — `instructions`:** This is a config key in `kilo.jsonc`. It takes an array of file glob patterns (e.g. `["rules/*.md"]` or `["~/shared/rules/*.md"]`). All matching files are loaded into every agent's system prompt at session start. These are the "always-loaded rules."

**How loading works:**
- `instructions` arrays **concatenate with dedup** across global + project `kilo.jsonc` files (per `mergeConfigConcatArrays` in the Kilo source). Adding `"instructions": ["~/Documents/code/kilo-config/rules/*.md"]` to the global config means every project gets those rules without each project needing to know about the path. The project's own `instructions` adds on top.
- Skills/agents/commands are **discovered by directory scanning** from ALL config directories (global, KILO_CONFIG_DIR, project). The `skill()` tool finds them by name regardless of which directory they live in.
- Zero-matching globs are silently ignored — no error, no warning. The shared rules path in the global config safely resolves to zero files on machines without the repo.

## Final Structure

```
~/Documents/code/kilo-config/
  rules/                    # always-loaded universal rules (keep short)
  skills/                   # loaded on demand for specific domains
    code-review-and-refactor/
    change-behavior/
    coding-standards/
    write-documentation/
  agent/                    # shared custom agents (empty for now)
  command/                  # shared commands (empty for now)

~/Documents/code/houses/
  .kilo/
    kilo.jsonc              # references both project + shared rules
    rules/                  # project-specific rules
      dev-server.md
    skills/                 # (empty — all skills moved to shared)
    command/
      dump-sheet.md         # Houses-specific command
  AGENTS.md                 # stays, project-specific
  docs/                     # stays, project-specific reference docs
    coding-standards.md     # canonical human reference (merged generic + houses)
    development.md
    writing-documentation.md
```

## Step-by-Step

### 1. Create directory structure

```bash
mkdir -p ~/Documents/code/kilo-config/{rules,skills,agent,command}
```

### 2. Move shareable rules (with minor edits)

From `houses/.kilo/rules/` to `kilo-config/rules/`:

| File | Edits needed |
|------|-------------|
| `code-review-graph.md` | None |
| `code-quality.md` | None |
| `fail-fast.md` | None |
| `session-start.md` | None |
| `stream-output.md` | Change "`make run`" → "the dev server" |
| `test-first.md` | Change "in this project" → "in this codebase" |
| `use-existing-tools.md` | Change `houses/sheets.py` example → generic example |
| `change-behavior.md` | Update skill path reference → shared location |
| `write-docs.md` | Rewrite: "When asked to write or update documentation, load the write-documentation skill" |

Delete these from `houses/.kilo/rules/` after moving.

### 3. Move shareable skills

From `houses/.kilo/skills/` to `kilo-config/skills/`:

| Skill | Notes |
|-------|-------|
| `code-review-and-refactor/` | No edits needed |
| `change-behavior/` | Major content updates (see below) |

Delete from `houses/.kilo/skills/` after moving.

#### Change-behavior skill: full rewrite

...

### 4. Create new skills

**`kilo-config/skills/coding-standards/skill.md`** — generic coding standards extracted from `docs/coding-standards.md`

**`kilo-config/skills/write-documentation/skill.md`** — adapted from `docs/writing-documentation.md`

### 5. Keep in `houses/.kilo/rules/`

| Rule | Reason |
|------|--------|
| `dev-server.md` | References project-specific `make run` |

### 6. Delete obsolete project files

- `houses/.kilo/rules/write-docs.md`
- `houses/.kilo/rules/read-coding-standards.md`
- `houses/.kilo/rules/change-behavior.md`
- `houses/.kilo/skills/change-behavior/`

### 7. Set up `KILO_CONFIG_DIR`

Add to `~/.zshrc`:
```bash
export KILO_CONFIG_DIR="$HOME/Documents/code/kilo-config"
```

### 8. Update `~/.config/kilo/kilo.jsonc` (global config)

```json
"instructions": [
  "~/Documents/code/kilo-config/rules/*.md"
]
```

### 9. Git init

```bash
cd ~/Documents/code/kilo-config
git init
git add -A
git commit -m "init: shared Kilo rules, skills, and agents"
```

## What Changes

| Before | After |
|--------|-------|
| `houses/.kilo/rules/` has 12 files | `houses/.kilo/rules/` has 1 file (dev-server) |
| `houses/.kilo/skills/` has 2 skills | `houses/.kilo/skills/` is empty |
| `docs/writing-documentation.md` is the only doc-writing guidance | Skill + rule live in shared config, doc stays for humans |
| `docs/coding-standards.md` is 340 lines | Generic parts extracted to shared skill, doc stays as human reference |
| All rules loaded for ALL agents regardless of role | Universal rules always-loaded (~20 tokens each), domain guidance loaded on demand via skills |
