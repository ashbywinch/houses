"""Regression: the unit-conftest autouse fixtures must establish test
isolation BEFORE any Services construction pushes default settings.

CI order-of-setup failure: _mock_google_routes called make_services()
without depending on the isolation fixtures, so SettingsNode.push ran
the write-guard against a non-app process and every unit test errored
at setup with 'Refusing to write settings from a non-app process'.
"""


def test_mock_google_routes_setup_runs_under_clean_guard(monkeypatch):
    """Reproduce the CI setup path with the guard fully armed."""
    monkeypatch.delenv("HOUSES_SCRIPTS_MAY_WRITE", raising=False)
    monkeypatch.setattr("houses.nodes.settings._app_mode", False)
    monkeypatch.setattr("dag.persistence.testing", False)

    from houses.services_provider import _request_services as _sp
    from tests.helpers import make_services

    token = _sp.set(make_services())
    try:
        svc = _sp.get()
        assert svc is not None
        assert svc.persons_source.latest_attempt().succeeded
    finally:
        _sp.reset(token)
