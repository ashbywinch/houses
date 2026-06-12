# Commute (Simon / Lorena)

Computes door-to-door public transport commute times and costs from a
property to Simon's work (SW1V 2QQ) and Lorena's work (EC3A 7LP).

- **Coordinators**: `houses/enricher.py` → `compute_simon_commute()`,
  `compute_lorena_commute()`, `compute_commute_breakdown()`
- **Routing dispatch**: `houses/routing.py` → `get_commute()`
- **TfL integration**: `houses/transit_route.py` → `TransitRoute`
- **Value objects**: `houses/commute.py` → `Commute`, `CostGroup`, etc.
- **Bus fares**: `houses/bus_journey.py` → `BusJourneyRegistry`

The algorithm tries Google Routes first (all UK buses), then TfL for
London areas, then driving (Simon only). Prefers the route with real
pricing data over a faster but unpriced estimate.
