"""Enforce layer boundaries — dag/ must not import houses/, etc."""

from archunitpython.layers import project_layers


def _format_violations(violations: list) -> str:
    lines = ["Layer violations found:"]
    for v in violations:
        dep = v.dependency
        lines.append(f"  {dep.source_label} → {dep.target_label}")
    return "\n".join(lines)


def test_dag_does_not_import_houses():
    la = project_layers()
    la = la.layer("dag").defined_by("./dag/*.py")
    la = la.layer("dag_tests").defined_by("./tests/unit/dag/*.py")
    la = la.layer("houses").defined_by("houses/**/*.py")
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
    la = la.layer("web").defined_by("houses/web/*.py")
    la = la.layer("sheets").defined_by("houses/sheets/*.py")
    la = la.where_layer("web").may_not_depend_on_layers("sheets")
    violations = la.check()
    assert len(violations) == 0, _format_violations(violations)
