"""Script shebangs must name an interpreter that exists.

2026-10-27: `tools/scrape_worker.py` began `#!/usr/bin/env python`, and `python`
is not on PATH on the dev box (only `python3`). Any direct execution — a git
hook, an editor, a cron line — failed with `error: command not found: python`
before the script's own first line. Every other tool in the repo already uses
`python3`; this holds the line so the outlier cannot come back.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT_DIRS = ("tools", "scripts")

# Names that do not exist on a stock PATH, or that only exist inside a venv.
BANNED = re.compile(rb"^#!\s*/usr/bin/env\s+python\s*$", re.MULTILINE)


# lucidlint: ignore fakefs — the subject IS the repo's real script files.
def test_no_script_uses_a_bare_python_shebang():
    offenders = [
        str(path.relative_to(REPO))
        for directory in SCRIPT_DIRS
        for path in (REPO / directory).rglob("*.py")
        if path.is_file() and BANNED.search(path.read_bytes()[:200])
    ]
    assert not offenders, f"shebang names `python`, which need not exist: {offenders}"
