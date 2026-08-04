"""Enforce layer boundaries — dag/ must not import houses/, etc."""

from pathlib import Path

from archunitpython.layers import project_layers

# archunitpython matches layer patterns against ABSOLUTE normalized file
# paths.  Relative globs ("./dag/*.py") compile to regexes anchored on
# "./" that match nothing — every edge then resolves to no layer and the
# check passes VACUOUSLY.  Derive absolute patterns from the repo root so
# the rules actually run.
ROOT = str(Path(__file__).resolve().parents[3])  # repo root (tests/unit/dag -> 3 up)


def _format_violations(violations: list) -> str:
    lines = ["Layer violations found:"]
    for v in violations:
        dep = v.dependency
        lines.append(f"  {dep.source_label} → {dep.target_label}")
    return "\n".join(lines)


def test_dag_does_not_import_houses():
    la = project_layers()
    la = la.layer("dag").defined_by(f"{ROOT}/dag/*.py")
    la = la.layer("dag_tests").defined_by(f"{ROOT}/tests/unit/dag/*.py")
    la = la.layer("houses").defined_by(f"{ROOT}/houses/*.py")  # fnmatch * crosses "/" — matches the whole tree
    la = la.where_layer("dag").may_only_depend_on_layers()
    la = la.where_layer("dag_tests").may_only_depend_on_layers("dag", "houses")
    violations = la.check()
    assert len(violations) == 0, _format_violations(violations)


def test_web_does_not_import_sheets():
    """HTTP layer must not import from the sheets module directly.

    All sheet data should reach the web layer through the DAG (computed
    values) or the comments DB (user-generated content).  Direct sheet
    access from HTTP handlers bypasses caching and the DAG lifecycle.
    """
    la = project_layers()
    la = la.layer("web").defined_by(f"{ROOT}/houses/web/*.py")
    la = la.layer("sheets").defined_by(f"{ROOT}/houses/sheets/*.py")
    la = la.where_layer("web").may_not_depend_on_layers("sheets")
    violations = la.check()
    assert len(violations) == 0, _format_violations(violations)
