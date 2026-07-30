# Provenance Redesign — Phase 1: Research & Gap Analysis

## Goal

Before designing anything, research the codebase to understand what every node in the provenance JSON actually represents in the domain. Then produce a gap analysis identifying every place where the current data model is insufficient for a naive user to understand the calculation.

## What to Research

Read these files to understand what each node type actually means:

1. `dag/attempt.py` (lines 235-291) — The Provenance dataclass and SourceType enum
2. `dag/derived_node.py` (lines 280-310) — The default `build_provenance()` that walks dependencies
3. `houses/nodes/property.py` — The PropertyNodes class that wires all nodes together (see which inputs connect to which calculations)
4. `houses/nodes/total_monthly_housing_cost_node.py` — The actual calculation for Total Monthly Cost
5. `houses/nodes/stamp_duty_node.py` — What Stamp Duty depends on and how it's calculated
6. `houses/nodes/equity_total_node.py` — How equity is calculated
7. `houses/nodes/life_insurance_node.py` — How life insurance is calculated
8. `houses/nodes/monthly_mortgage_payment_node.py` — How monthly mortgage is calculated
9. `houses/nodes/commute_breakdown_node.py` — How commute breakdown is aggregated
10. `houses/nodes/epc_node.py` — How EPC is looked up
11. `houses/nodes/council_tax_node.py` — How Council Tax is looked up
12. `houses/services.py` — What the `financial` and `persons` config sources contain
13. `houses/frontend/src/components/ProvenanceView.vue` — The current component
14. `houses/frontend/src/components/ProvenanceTree.vue` — The old component

## What to Produce

Write `tmp/provenance-v5-gap-analysis.md` containing:

### Part A: Node Catalog
For each of the 4 datasets, list every node with:
- Its label, sourceType, and key
- What it ACTUALLY represents in the domain (after reading the code)
- What information it provides (value, formula, freshness, URL)
- What information is MISSING that a naive user would need

### Part B: Chain Traceability
For each dataset, trace the full calculation chain from root to leaves, noting every link where:
- A value is calculated but the formula/inputs aren't visible
- A config/settings node exists but its individual values aren't shown
- A node has an opaque internal name with no domain explanation
- An error/gap exists and the user can't tell what went wrong
- A node is duplicated across multiple parents

### Part C: Missing Data Catalog
List every place where the current provenance data model is insufficient. For each:
- What information is missing
- What a naive user would need instead
- Whether this is a backend field change or the data exists but the component doesn't surface it

## Data Files

The 4 datasets are in `tmp/provenance-v4-full-brief.md` under "Data Files (4 scenarios, ONE generic component renders all)". The JSON starts at "### A: Total Monthly Cost" through "### D: EPC Rating".
