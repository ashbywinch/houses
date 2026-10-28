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
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Span:
    """A half-open [start, end) region of a document's text."""

    start: int
    end: int


class DocsLinks:
    """The link check for one repository root.

    The root and the skip rules are instance state rather than module globals:
    the check is then runnable against a fixture tree, and nothing binds at
    import time.
    """

    LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
    FENCE_RE = re.compile(r"^```", re.MULTILINE)
    SKIP_PREFIXES = (
        "http://",
        "https://",
        "#",
        "skill://",
        "rule://",
        "agent://",
        "memory://",
        "artifact://",
    )

    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def doc_files(self) -> list[Path]:
        files = list((self.repo / "docs").rglob("*.md"))
        for name in ("README.md", "AGENTS.md"):
            if (self.repo / name).exists():
                files.append(self.repo / name)
        return sorted(files)

    def outside_fences(self, text: str) -> list[Span]:
        """The spans of `text` that are NOT inside ``` fenced blocks."""
        spans: list[Span] = []
        pos = 0
        in_fence = False
        seg_start = 0
        for line in text.splitlines(keepends=True):
            if self.FENCE_RE.match(line):
                if in_fence:
                    spans.append(Span(seg_start, pos))
                else:
                    seg_start = pos + len(line)
                in_fence = not in_fence
            pos += len(line)
        if not in_fence:
            spans.append(Span(seg_start, pos))
        return spans

    def check(self) -> list[str]:
        return [problem for md in self.doc_files() for problem in self.file_problems(md)]

    def file_problems(self, md: Path) -> list[str]:
        text = md.read_text(encoding="utf-8")
        return [
            problem for span in self.outside_fences(text) for problem in self._check_span(md, text, span)
        ]

    def _check_span(self, md: Path, text: str, span: Span) -> list[str]:
        problems: list[str] = []
        for match in self.LINK_RE.finditer(text, span.start, span.end):
            url = match.group(2).strip()
            if url.startswith(self.SKIP_PREFIXES) or "=" in url or " " in url:
                continue
            if "{{" in url or "$" in url:
                continue
            if "#" in url:
                url = url[: url.index("#")]
            if not url:
                continue
            target = (md.parent / url).resolve()
            rel = md.relative_to(self.repo)
            if not target.is_relative_to(self.repo):
                problems.append(f"{rel}: link '{match.group(1)}' -> {url} (outside repo)")
            elif not target.exists():
                problems.append(f"{rel}: link '{match.group(1)}' -> {target.relative_to(self.repo)} (missing)")
        return problems

    def run(self) -> int:
        failures = self.check()
        if failures:
            print("houses docs self-check failed:")
            for failure in failures:
                print(f"  {failure}")
            return 1
        print(f"docs self-check ok ({len(self.doc_files())} files)")
        return 0


def main() -> int:
    return DocsLinks(Path(__file__).resolve().parent.parent).run()


if __name__ == "__main__":
    sys.exit(main())
