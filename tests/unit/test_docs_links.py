import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

DOC_FILES: list[Path] = [
    REPO_ROOT / "AGENTS.md",
    *sorted((REPO_ROOT / "docs").rglob("*.md")),
]


def _relative_links(md_file: Path):
    text = md_file.read_text(encoding="utf-8")
    for match in LINK_RE.finditer(text):
        url = match.group(2).strip()
        if url.startswith("http://") or url.startswith("https://") or url.startswith("#"):
            continue
        if "=" in url or " " in url:
            continue
        # Skip template variables ($1, $2, $ARGUMENTS) used in command files
        if "#$" in url:
            continue
        if "#" in url:
            url = url[: url.index("#")]
        target = (md_file.parent / url).resolve()
        yield match.group(1), target


class TestDocLinks(unittest.TestCase):
    def test_all_relative_links_resolve(self):
        failures: list[str] = []
        for md_file in DOC_FILES:
            for link_text, target in _relative_links(md_file):
                if not target.is_relative_to(REPO_ROOT):
                    rel_source = md_file.relative_to(REPO_ROOT)
                    failures.append(f"{rel_source}: link '{link_text}' -> {target} (outside repo)")
                elif not target.exists():
                    rel_source = md_file.relative_to(REPO_ROOT)
                    rel_target = target.relative_to(REPO_ROOT)
                    failures.append(f"{rel_source}: link '{link_text}' -> {rel_target}")
        self.assertEqual(failures, [], f"\n{chr(10)}".join(failures))
