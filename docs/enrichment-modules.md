# Enrichment Modules

Each module's dedicated doc covers its purpose, module location, and
graceful degradation behaviour.

| Module | Doc | Entry Point |
|--------|-----|-------------|
| Commute | [commute.md](commute.md) | `compute_simon_commute()`, `compute_lorena_commute()` |
| Petrol Cost | [petrol-cost.md](petrol-cost.md) | `compute_petrol_cost()` |
| Rail Fare Fallback | [rail-fares.md](rail-fares.md) | `nearest_station()`, `fare_between()` |
| Schools | [schools.md](schools.md) | `compute_school_commute()` |
| Walkability | [walkability.md](walkability.md) | `enrich_walkability()` |
| Town Description | [town-description.md](town-description.md) | `generate_town_description()` |
| Council Tax | [council-tax.md](council-tax.md) | `lookup_council_tax()` |
| EPC Rating | [epc.md](epc.md) | `lookup_epc()` |
| Commute Breakdown | [commute-breakdown.md](commute-breakdown.md) | `compute_commute_breakdown()` |

## Adding a New Module

See [adding-a-new-enrichment-module.md](adding-a-new-enrichment-module.md).
