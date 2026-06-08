# Code Quality Rule

When asked to review code, improve code quality, refactor code, or clean up the codebase (without changing functionality) — or when the task description implies any of these — load the `code-review-and-refactor` skill via `skill("name": "code-review-and-refactor")` and follow its process.

This applies to requests like:
- "Review this file / module / project"
- "Make this code better"
- "Refactor this function / class"
- "Clean up this code"
- "This code doesn't follow our standards"
- "Improve the naming / structure / types here"

It does NOT apply when the user explicitly asks to change functionality, add a feature, or fix a bug. For those, use the normal development workflow (AGENTS.md decision tree).

The skill uses the `code-review-graph` CLI (`uvx code-review-graph`) for analysis. Ensure the graph is built before starting — see Phase 1 in the skill.
