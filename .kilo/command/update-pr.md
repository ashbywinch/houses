---
description: Update a GitHub PR's body with markdown content
---

# /update-pr

Update a pull request's description with the given text. Uses the REST API
because `gh pr edit --body` silently fails on some repos (returns exit 0 but
doesn't update the body).

## Usage

```bash
# Update PR #30 with text from a file
/update-pr 30 body.txt

# Or pipe markdown directly
cat << 'EOF' | /update-pr 30 /dev/stdin
## Summary
...
EOF
```

The agent will:
1. Read the body text from the file or stdin
2. Call `gh api repos/:owner/:repo/pulls/N --method PATCH -f body=@file`
3. Verify the body was updated by reading it back

## Why not gh pr edit

`gh pr edit --body "$text"` appears to succeed (exit 0, no error message)
but leaves the body unchanged. This is a known issue with some GitHub
configurations. The REST API approach always works.
