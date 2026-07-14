from __future__ import annotations

import pytest


class TestDecomposedSources:
    @pytest.fixture
    def _svc(self):
        from houses.context import get_services
        try:
            return get_services()
        except LookupError:
            from houses.services import Services
            svc = Services()
            from houses.services_provider import _request_services as _sp
            _sp.set(svc)
            return svc

    @pytest.mark.asyncio
    async def test_persons_source_has_defaults(self, _svc):
        a = await _svc.persons_source.attempt()
        assert a.succeeded
        persons = a.value_or_none()
        assert len(persons) == 3
        simon = persons[0]
        assert simon.name == "Simon"
        assert simon.bus_walk_penalty_minutes == 20
        assert len(simon.places_of_interest) == 3
        george = persons[2]
        assert george.name == "George"
        assert george.is_child is True
        assert len(george.places_of_interest) == 2

    @pytest.mark.asyncio
    async def test_lorena_has_two_trips(self, _svc):
        a = await _svc.persons_source.attempt()
        lorena = a.value_or_none()[1]
        lorena_office = lorena.places_of_interest[0]
        assert lorena_office.label == "Aldgate"
        assert lorena_office.trips_per_week == 2

    @pytest.mark.asyncio
    async def test_financial_source_has_defaults(self, _svc):
        a = await _svc.financial_source.attempt()
        assert a.succeeded
        fin = a.value_or_none()
        assert fin["mortgage_rate"] == 0.045
        assert fin["working_weeks_per_year"] == 46

    @pytest.mark.asyncio
    async def test_commute_thresholds_source(self, _svc):
        a = await _svc.commute_thresholds_source.attempt()
        assert a.succeeded
        thresh = a.value_or_none()
        assert "Simon" in thresh
        assert "Lorena" in thresh
        assert thresh["Simon"]["good_max_minutes"] == 30
