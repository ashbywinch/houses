## Edit Protocol

### Before every edit:
1. Re-read the target lines: `read path/to/file.py:START-END`
2. Copy the `[path#TAG]` from the read output.
3. Use `SWAP N.=M:` where N‑M cover ONLY the lines that change.
4. Body rows use `+` only. NEVER write `-` lines.

### When an edit fails:
5. STOP. Read the error message.
6. Re-read the file with a ranged selector before retrying.
7. NEVER retry the same pattern — the error tells you what to fix.

### Remember:
- `read` without a selector returns a SUMMARY with `…` in elided bodies.
- You CANNOT edit lines inside `…`. Use a ranged read like `file.py:10-20` first.
- After any successful edit, re-read the file before the next edit to it.
