## Edit Protocol

### Before every edit:

1. **Read the file:** `read path/to/file.py`

2. **Can you see ALL the lines you need to edit?**
   - YES → the output shows your target lines. Copy `[path#TAG]` and proceed.
   - NO  → the output has `…` or shows only a structural summary.
           Read the exact range first: `read path/to/file.py:START-END`
           Now copy `[path#TAG]` from this ranged read.
           You CANNOT edit lines you haven't read.

3. **Write one hunk:** Use `SWAP N.=M:` where N‑M cover ONLY the lines
   that change. (If only line 42 changes: `SWAP 42.=42:`)
   Body rows use `+` only. NEVER use `-` lines.

### When an edit fails, read the error — it tells you the fix:

| Error message | Cause | Fix |
|---|---|---|
| `body ends by restating` | SWAP range wider than changed lines | Shrink range to ONLY changed lines |
| `file changed between read and edit` | File was modified since your read | Re-read the file, get a fresh tag |
| `hash XXXX is not from this session` | Tag from a previous session | Run read again |
| `cannot edit lines inside` | Lines were never displayed | Ranged read first, then edit |
| `stale snapshot tag` | Another edit modified this file | Re-read before next edit |

After any failure: **NEVER retry the exact same edit.** The error tells
you what to change — do that, then re-read before retrying.

### After every successful edit:

6. Re-read the file before editing it again. The tag and line numbers
   changed — your next edit needs fresh coordinates.
