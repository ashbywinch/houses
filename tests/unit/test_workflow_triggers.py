"""Workflow triggers that must not fire on a release tag.

GitHub does not evaluate ``paths`` filters for tag pushes. A ``push``
trigger narrowed by path but not by branch therefore runs on every release
tag, and its path guard is void — 2026-10-29: tagging v1.6.0 ran the bake
pipeline, which dispatched ``action=bake`` and sat on the ``houses-terraform``
concurrency group ahead of the rollout's ``provision``.

PyYAML arrives with ``uvicorn[standard]`` (a runtime dependency), so the
tests read the real workflow files rather than a copy of their text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _on(workflow: Path) -> dict[str, Any]:
    """The workflow's ``on:`` block — YAML 1.1 parses the bare key as ``True``."""
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    block = document.get(True, document.get("on", {}))
    return block if isinstance(block, dict) else {}


def test_a_path_filtered_push_names_its_branch() -> None:
    """A path filter cannot be the only narrowing: it is not evaluated for
    tag pushes, so the trigger would also fire on every release tag."""
    offenders: list[str] = []
    for workflow in WORKFLOWS.glob("*.yml"):
        push = _on(workflow).get("push")
        if not isinstance(push, dict):
            continue
        path_narrowed = "paths" in push or "paths-ignore" in push
        branch_narrowed = "branches" in push or "branches-ignore" in push
        if path_narrowed and not branch_narrowed:
            offenders.append(workflow.name)

    assert offenders == [], (
        "push triggers with a paths filter must also name branches — GitHub does not "
        f"evaluate path filters for tag pushes, so these fire on every release tag: {offenders}"
    )


def test_bake_is_main_only() -> None:
    """The image is baked from main; a release tag must never start a bake."""
    assert _on(WORKFLOWS / "bake.yml")["push"]["branches"] == ["main"]
