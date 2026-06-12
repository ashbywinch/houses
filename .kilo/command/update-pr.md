---
description: Update a PR body — use instead of gh pr edit which silently fails
agent: code
subtask: true
---

Update PR #$1 with body text from $2 (file path or /dev/stdin for piped input).

Use `gh api repos/:owner/:repo/pulls/$1 --method PATCH -f body=@$2` to update the body.

Do NOT use `gh pr edit --body` — it silently succeeds without updating on some repos.

Verify the body was updated by reading it back with `gh pr view $1 --json body`.
