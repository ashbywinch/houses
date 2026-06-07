---
name: change-behavior
description: |
  Translate behavior-change requests into rule, skill, command, or AGENTS.md
  edits, then draft the changes and prepare a PR.
---

Use the companion rule (`.kilo/rules/change-behavior.md`) to decide **when** to invoke this skill. Use this skill to decide **how** to carry it out.

1. **Map the request to the right artifact type.**
   - **Rule** (`.kilo/rules/*.md`) — agent behavior, preferences, policies that govern how the agent acts.
   - **Skill** (`.kilo/skills/<name>/<name>.md`) — reusable capability or helper action the agent performs.
   - **Command** (`.kilo/command/*.md`) — repeatable standard operation (e.g. submitting a PR, running tests).
   - **AGENTS.md** — project conventions, process notes, server behavior, or knowledge the agent should always have.
   - Prefer updating an existing artifact over creating a new one. Skills and rules work together: skills provide abilities, rules define when to use them.

   Examples:
   - "Make the agent prefer short answers and more comments in code." → rule.
   - "Add a reusable helper that summarizes rule changes and lists affected files." → new or updated skill, plus rule if needed.
   - "Create a PR for this rule change and run tests." → command.
   - "The dev server uses ``--reload``, we never restart it manually." → rule.

2. **Understand the request clearly before writing.**
   - Pin down which aspect is changing: behaviour, tooling, style, or project convention.
   - Ask a concise clarifying question if the request is ambiguous.

3. **Review existing artifacts in `.kilo/`.**
   - Check rules, skills, commands, and AGENTS.md for anything already covering the request.
   - Propose a precise update to a matching artifact if applicable, otherwise propose creating a new one.

4. **Draft the change using positive language.**
   - Prefer imperative "DO" statements over prohibitions.
   - Use examples to illustrate patterns to avoid (rather than writing "DON'T" rules).
   - Example: write "Wait for the reload log message before testing changes" instead of "Do not restart the server".
   - Keep language short and specific. Policy belongs in rules, process belongs in skills.


---

### Skill naming guide

A skill file lives at ``.kilo/skills/<name>/<name>.md`` where ``<name>`` is a short kebab-case identifier. The name should:
- Describe the skill's purpose, not a generic word like "skill" or "process".
- Distinguish the skill from the rule that governs when to use it.
- Example: ``change-behavior.md`` is better than ``SKILL.md`` or ``behavior.md``.

A well-named skill paired with a well-named rule makes both discoverable at a glance:
- ``.kilo/skills/change-behavior/change-behavior.md`` — how to do it.
- ``.kilo/rules/change-behavior.md`` — when to do it.
