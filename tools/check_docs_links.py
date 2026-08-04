"""Repo self-check: every relative markdown link resolves (stdlib only).

Copied from omp-config's `tools/check_docs_links.py` (house standard —
skill://new-repo-scaffold), adapted to this repo: docs/ + README.md +
AGENTS.md only (no rules/skills/profiles here, so no skill well-formedness
check).

Skips http(s), #anchors, harness URIs (skill://, rule://, agent://,
memory://, artifact://), template variables, and links inside fenced code
blocks. A doc that links nowhere is a finding.

Wired into `make docs-check` and CI.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
FENCE_RE = re.compile(r"^```", re.MULTILINE)
SKIP_PREFIXES = ("http://", "https://", "#", "skill://", "rule://", "agent://", "memory://", "artifact://")


def _doc_files() -> list[Path]:
    files = [p for p in (REPO / "docs").rglob("*.md")]
    for name in ("README.md", "AGENTS.md"):
        if (REPO / name).exists():
            files.append(REPO / name)
    return sorted(files)


def _outside_fences(text: str) -> list[tuple[int, int]]:
    """(start, end) spans of the text that are NOT inside ``` fenced blocks."""
    spans: list[tuple[int, int]] = []
    pos = 0
    in_fence = False
    seg_start = 0
    for line in text.splitlines(keepends=True):
        if FENCE_RE.match(line):
            if in_fence:
                spans.append((seg_start, pos))
            else:
                seg_start = pos + len(line)
            in_fence = not in_fence
        pos += len(line)
    if not in_fence:
        spans.append((seg_start, pos))
    return spans


def _check_links() -> list[str]:
    failures: list[str] = []
    for md in _doc_files():
        text = md.read_text(encoding="utf-8")
        spans = _outside_fences(text)
        for span_start, span_end in spans:
            for match in LINK_RE.finditer(text, span_start, span_end):
                url = match.group(2).strip()
                if url.startswith(SKIP_PREFIXES) or "=" in url or " " in url or "{{" in url or "$" in url:
                    continue
                if "#" in url:
                    url = url[: url.index("#")]
                if not url:
                    continue
                target = (md.parent / url).resolve()
                rel = md.relative_to(REPO)
                if not target.is_relative_to(REPO):
                    failures.append(f"{rel}: link '{match.group(1)}' -> {url} (outside repo)")
                elif not target.exists():
                    failures.append(f"{rel}: link '{match.group(1)}' -> {target.relative_to(REPO)} (missing)")
    return failures


def main() -> int:
    failures = _check_links()
    if failures:
        print("houses docs self-check failed:")
        for f in failures:
            print(f"  {f}")
        return 1
    print(f"docs self-check ok ({len(_doc_files())} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
