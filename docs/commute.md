# Commute (Simon / Lorena)

Door-to-door public transport commute times and costs from a property to Simon's work (SW1V 2QQ) and Lorena's work (EC3A 7LP).

## Algorithm

The routing algorithm (in `houses/routing.py` → `get_commute()`) tries, in order:

1. **Google Routes first** — all UK buses
2. **TfL** for London areas
3. **Driving** (Simon only — he has a car)

Prefers the route with **real pricing data** over a faster but unpriced estimate.

## Structure

Implementation is split across modules; entry points are discoverable via the code-review graph:

- `houses/enricher.py` — commute coordination
- `houses/routing.py` — routing dispatch
- `houses/transit_route.py` — TfL integration
- `houses/commute.py` — `Commute`, `CostGroup` value objects
- `houses/bus_journey.py` — `BusJourneyRegistry` fare data
