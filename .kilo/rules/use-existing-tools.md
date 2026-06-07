# Use Existing Tools Before Writing Ad-Hoc Code

When inspecting sheet data or performing standard operations, check `.kilo/command/`, `.kilo/scripts/`, and `scripts/` for an existing tool before writing inline Python.

- Use `/dump-sheet` to inspect sheet tab contents with specified columns — do not write `uv run python -c "from houses.sheets import..."` inline.
- Use existing scripts in `scripts/` before writing one-off code.
- If the existing tool is missing a feature you need, consider improving it rather than creating from scratch.

This keeps output consistent and avoids reinventing the same queries every session.
