# Enrichment Modules

Index of enrichment modules and their dedicated docs. Entry points are discoverable in the code (each module's file is listed; use the code-review graph `file_summary` for symbols).

| Module | Dedicated doc | Implementation |
|--------|---------------|----------------|
| Commute | [commute.md](commute.md) | `houses/enricher.py` |
| Petrol Cost | — (no doc; see `houses/nodes/petrol.py`) | `houses/nodes/petrol.py` |
| Rail Fare Fallback | — (no doc; see `houses/nodes/rail_fare_node.py`) | `houses/nodes/rail_fare_node.py` |
| Schools | — (no doc; see `houses/nodes/schools.py`) | `houses/nodes/schools.py` |
| Walkability | — (no doc; see `houses/walkability.py`) | `houses/walkability.py` |
| Town Description | — (no doc; see `houses/town_desc.py`) | `houses/town_desc.py` |
| Council Tax | — (no doc; see `houses/council_tax.py`) | `houses/council_tax.py` |
| EPC Rating | — (no doc; see `houses/nodes/epc_node.py`) | `houses/nodes/epc_node.py` |
| Commute Breakdown | — (no doc; see `houses/nodes/commute_breakdown_node.py`) | `houses/nodes/commute_breakdown_node.py` |

## Adding a New Module

See [adding-a-new-enrichment-module.md](adding-a-new-enrichment-module.md).
