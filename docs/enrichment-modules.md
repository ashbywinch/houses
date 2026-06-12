# Enrichment Modules

Each enrichment module transforms a raw property payload into structured data. All modules are designed to fail gracefully — if an API is unavailable or misconfigured, they log a warning and return `None` or empty defaults.

Click through to each module's dedicated doc for details on API endpoints, data sources, column mapping, and graceful degradation.

| Module | Doc | Purpose |
|--------|-----|---------|
| Transit Commute | [commute.md](commute.md) | Door-to-door public transport commute times/costs for Simon and Lorena (Google Routes + TfL) |
| Petrol Cost | [petrol-cost.md](petrol-cost.md) | Daily petrol cost for driving to Bracknell via ORS |
| Rail Fare Fallback | [rail-fares.md](rail-fares.md) | National Rail fare estimates when TfL has no pricing |
| Schools | [schools.md](schools.md) | Nearest primary/secondary schools accepting boys, non-fee-paying |
| Walkability | [walkability.md](walkability.md) | Walk time to town centre and nearby amenities |
| Town Description | [town-description.md](town-description.md) | LLM-generated single-sentence neighbourhood description |
| Council Tax | [council-tax.md](council-tax.md) | Council tax band and yearly cost via VOA scraper |
| Commute Breakdown | [commute-breakdown.md](commute-breakdown.md) | Yearly commute cost from individual daily costs |
| EPC Rating | [epc.md](epc.md) | Energy Performance Certificate band via gov.uk API |

## Adding a New Module

See [adding-a-new-enrichment-module.md](adding-a-new-enrichment-module.md) for the step-by-step guide on creating and wiring in a new enrichment module.
