# Enrichment Modules

Each module's dedicated doc covers its purpose, module location, and
graceful degradation behaviour.

| Module | Doc | Entry Point |
|--------|-----|-------------|
| Commute | [commute.md](commute.md) | `compute_simon_commute()`, `compute_lorena_commute()` |
| Petrol Cost | (doc removed — implementation in `houses/nodes/petrol.py`) | `compute_petrol_cost()` |
| Rail Fare Fallback | (doc removed — implementation in `houses/nodes/rail_fare_node.py`) | `nearest_station()`, `fare_between()` |
| Schools | (doc removed — implementation in `houses/nodes/schools.py`) | `compute_school_commute()` |
| Walkability | (doc removed — implementation in `houses/walkability.py`) | `enrich_walkability()` |
| Town Description | (doc removed — implementation in `houses/town_desc.py`) | `generate_town_description()` |
| Council Tax | (doc removed — implementation in `houses/council_tax.py`) | `lookup_council_tax()` |
| EPC Rating | (doc removed — implementation in `houses/nodes/epc_node.py`) | `lookup_epc()` |
| Commute Breakdown | (doc removed — implementation in `houses/nodes/commute_breakdown_node.py`) | `compute_commute_breakdown()` |

## Adding a New Module

See [adding-a-new-enrichment-module.md](adding-a-new-enrichment-module.md).
