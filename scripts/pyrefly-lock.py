#!/usr/bin/env python3
"""Pyrefly baseline LOCK — both-direction drift detection on top of pyrefly.

Pyrefly's built-in baseline is a one-way filter: it suppresses errors that
match the baseline but never checks whether a baseline entry is STALE (an
error the code no longer produces). basedpyright's lock mode fails on drift
in BOTH directions (new error OR stale baseline entry) — that property
caught a real CI regression (a fix removed a diagnostic without refreshing
the baseline, and lock mode failed "went down by 1").

This wrapper restores the both-direction contract on top of pyrefly's fast
engine (~0.9s vs basedpyright's ~13s):

  check:
    - run `pyrefly check --output-format json` (NO baseline — we want every
      error, suppressed or not)
    - diff the current error set against .pyrefly-baseline.json:
        * errors in current but NOT baseline  -> fail "new errors"  (same
          as pyrefly's built-in gate)
        * errors in baseline but NOT current -> fail "stale baseline
          entry — run update-baseline"
    - exit 0 only when the two sets are identical

  update-baseline:
    - run the same check and write .pyrefly-baseline.json from the current
      errors (the committed contract)

The error key is (path, line, column, name) — same schema as pyrefly's own
baseline file, so update-baseline output is directly reusable.

Usage:
  scripts/pyrefly-lock.py check [--baseline FILE] [--pyrefly-config TOML]
  scripts/pyrefly-lock.py update-baseline [--baseline FILE] [--pyrefly-config TOML]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_BASELINE = Path(".pyrefly-baseline.json")


def _pyrefly_binary() -> str:
    """Prefer the project venv's pyrefly, else PATH (uv tool installs)."""
    venv = Path(".venv/bin/pyrefly")
    if venv.is_file():
        return str(venv)
    return "pyrefly"


# Extra args passed to every pyrefly invocation.
PYREFLY_BASE_ARGS = [_pyrefly_binary(), "check", "--output-format", "json"]


def error_key(e: dict) -> tuple:
    return (e.get("path"), e.get("line"), e.get("column"), e.get("name"))


def current_errors(extra_args: list[str]) -> list[dict]:
    """Run pyrefly (no baseline) and return the full error list."""
    proc = subprocess.run(
        PYREFLY_BASE_ARGS + extra_args,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):
        # pyrefly exits 1 when errors exist (expected); anything else is a
        # real failure (config, crash) — surface it.
        sys.stderr.write(proc.stderr or proc.stdout)
        sys.exit(proc.returncode)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(f"pyrefly returned non-JSON output:\n{proc.stdout}\n{proc.stderr}\n")
        sys.exit(2)
    return data.get("errors", [])


def load_baseline(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return json.loads(path.read_text()).get("errors", [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("check", "update-baseline"):
        p = sub.add_parser(name)
        p.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
        p.add_argument("--pyrefly-config", type=Path, default=None,
                       help="pyrefly.toml path (passed as -c when given)")
    args = parser.parse_args()

    extra = ["-c", str(args.pyrefly_config)] if args.pyrefly_config else []

    errors = current_errors(extra)
    current = {error_key(e): e for e in errors}
    baseline = {error_key(e): e for e in load_baseline(args.baseline)}

    if args.cmd == "update-baseline":
        # Sort by (path, line, column) for a stable diff-friendly file.
        ordered = sorted(errors, key=lambda e: (e.get("path", ""), e.get("line", 0), e.get("column", 0)))
        args.baseline.write_text(json.dumps({"errors": ordered}, indent=1) + "\n")
        print(f"pyrefly-lock: wrote baseline with {len(ordered)} errors to {args.baseline}")
        return 0

    # check: both directions must match.
    new_keys = current.keys() - baseline.keys()
    stale_keys = baseline.keys() - current.keys()

    if new_keys:
        print(f"pyrefly-lock: {len(new_keys)} NEW error(s) not in the baseline:")
        for k in sorted(new_keys):
            e = current[k]
            print(f"  {e.get('path')}:{e.get('line')}:{e.get('column')} "
                  f"[{e.get('name')}] {e.get('concise_description', '')}")
        print("  Fix: fix the code, or run: scripts/pyrefly-lock.py update-baseline")

    if stale_keys:
        plural = "y" if len(stale_keys) == 1 else "ies"
        print(f"pyrefly-lock: {len(stale_keys)} STALE baseline entr{plural} "
              f"(error no longer produced — the code changed without refreshing the baseline):")
        for k in sorted(stale_keys):
            e = baseline[k]
            print(f"  {e.get('path')}:{e.get('line')}:{e.get('column')} [{e.get('name')}]")
        print("  Fix: run: scripts/pyrefly-lock.py update-baseline && commit the refresh")

    if new_keys or stale_keys:
        return 1

    n = len(baseline)
    print(f"pyrefly-lock: clean — {n} baselined error{'s' if n != 1 else ''} match the current code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
