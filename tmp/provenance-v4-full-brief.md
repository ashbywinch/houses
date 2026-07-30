# Provenance Redesign — Full Brief (v4)

## Process

1. Read this entire brief, including all requirements and all data
2. Build a Vue 3 single-file component prototype (`.vue` file — `<template>`, `<script setup>`, `<style scoped>`)
3. Open it in a browser, set viewport to 400px wide, take a screenshot
4. Assess the screenshot against every user requirement (R1–R8)
5. If any requirement is not fully met: fix the prototype, re-screenshot, re-assess
6. Loop until ALL requirements pass
7. Write a brief explanation of why you believe each requirement is satisfied
8. Output the final prototype to `tmp/ProvenanceView.v4.vue` — a complete Vue 3 SFC that can be dropped into the project

## What Went Wrong Before (3 failed attempts)

**Attempt 1 (story-flow cards):** Designer replaced the tree with a linear card flow. Dropped most nodes — showed the mortgage result but not HOW it was calculated. Used aspirational text that doesn't exist in the data. User couldn't trace £2,917 → mortgage → interest rate + term. The entire point of provenance (traceability) was destroyed.

**Attempt 2 (cost breakdown + badges):** Added a horizontal cost bar and confidence badges. Same data-dropping problem. The cost bar didn't help trace the calculation — it just showed proportions. Didn't improve rendering of what's actually there. Added visual polish on top of the same missing information.

**Attempt 3 (flat list of all nodes):** Preserved every node but lost all structure. Just a pile of labeled items. No indication of what connects to what. Same problem as the old tree, just differently bad. User cannot tell what's an input vs a calculation vs a result.

**Root cause across all three:** Every designer treated "show every node" and "show how they connect" as a trade-off rather than requirements that must BOTH be met.

---

## User Requirements

### R1: A stranger must be able to explain the FULL calculation chain
Show the prototype to someone who has never seen the app, knows nothing about property, and is not mathematically confident. They must be able to point at "£2,917/mo" and — after exploring the interface — explain the entire chain:

"That's the mortgage plus the commute plus the council tax plus... the mortgage is £2,305/month, which comes from borrowing £415,000 at 4.95% over 27 years, and the £415,000 is the £800,000 property price plus £27,500 stamp duty minus £477,000 equity from selling their current home..."

Not everything needs to be visible at once — strategic hiding is fine. But every intermediate value must be discoverable within one tap/click from the thing it feeds into. If a value exists anywhere in the chain but a naive user cannot find it, the design fails.

### R2: Every value is traceable to its origin
The user must be able to follow any displayed value back through every intermediate step to its original source. The chain must be complete — no gaps where "this was calculated" but the inputs to that calculation are hidden.

### R3: The structure of the calculation is visible
The user must be able to see which things are:
- Inputs (user-entered: Rightmove price, renovation estimates, financial settings)
- API lookups (TfL, National Rail, Council Tax band, EPC register)
- Calculations (mortgage payment derived from price + rate + term)
- Intermediate values (mortgage required = price + stamp duty + works − equity)
- Results (total monthly cost)

And how they connect. Not told in a paragraph — shown visually. The relationship between nodes must be apparent.

### R4: Errors and gaps are visible and locatable
When a calculation fails (e.g. council tax lookup from ambiguous address), the user must be able to see:
- That it failed
- Where in the chain it failed
- What data was missing or what went wrong

When data is simply absent (commute cost has no value, no formula), that absence must be visible as a gap, not silently hidden.

### R5: Technical terms do not appear unexplained
If the user sees "stamp duty," "sinking fund," "equity," "mortgage term," "interest rate," or any other domain term, they must either already understand it or be able to get an explanation from within the display. No assumed domain knowledge. This can be inline, on hover, on tap, or in a glossary — but it must be there.

### R6: Freshness is per-input
The user must be able to see, for each individual source, how recently it was fetched or entered. Not one global badge over everything. A Rightmove price entered yesterday, an EPC rating from 8 days ago, and a commute cost from today must each show their own age.

### R7: Works at phone width AND desktop
Same information, same comprehensibility, at 400px wide (phone portrait) and 1400px wide (desktop). The design must be responsive to viewport width.

### R8: Aspirational — invent what's missing
The current data model is missing critical information. The prototype MUST show text, labels, explanations, annotations, and fields that don't exist yet. Every place where the current data is insufficient, invent what WOULD be needed. Annotate each invented element with "✨ Would require backend field: [field_name]".

The goal is to discover what's missing, not to constrain to what's there. If you can't make the calculation understandable with the current data, design what data you WOULD need. Use your common sense and knowledge of what an uninformed house buyer needs to understand.

---

## Data Files (4 scenarios, ONE generic component renders all)

### A: Total Monthly Cost — complex, with errors and duplicates

```json
{
  "label": "Total Monthly Cost",
  "description": "89306649/total_monthly_cost: dep failed (Works estimate required for: Ashby; no council tax data)",
  "sourceType": "calc",
  "freshness": "2026-07-30T07:21:57.139579+00:00",
  "status": "impossible",
  "error": "Works estimate required for: Ashby; no council tax data",
  "value": null,
  "sources": {
    "89306649/monthly_mortgage": {
      "label": "Monthly Mortgage",
      "description": "Works estimate required for: Ashby",
      "sourceType": "calc",
      "freshness": "2026-07-30T07:21:56.979960+00:00",
      "sources": {
        "89306649/mortgage_required": {
          "label": "Mortgage Required",
          "description": "Works estimate required for: Ashby",
          "sourceType": "calc",
          "freshness": "2026-07-30T07:21:56.979845+00:00",
          "sources": {
            "89306649/rightmove_price": {
              "label": "Rightmove",
              "value": "GBP 800,000.00",
              "sourceType": "user"
            },
            "89306649/stamp_duty": {
              "label": "Stamp Duty",
              "value": "GBP 27,500.00",
              "sourceType": "calc",
              "freshness": "2026-07-30T07:21:56.979513+00:00",
              "formula": { "lines": [
                  { "label": "Property Price", "value": "GBP 800,000.00" },
                  { "label": "First-time buyer relief", "value": "N/A" }
                ], "result": "GBP 27,500.00" },
              "sources": {
                "89306649/rightmove_price": { "label": "Rightmove", "value": "GBP 800,000.00", "sourceType": "user" },
                "89306649/status": { "label": "", "sourceType": "user" }
              }
            },
            "89306649/total_works": {
              "label": "Total Works",
              "description": "Works estimate required for: Ashby",
              "sourceType": "calc",
              "freshness": "2026-07-30T07:21:56.979586+00:00",
              "sources": {
                "persons": { "label": "db", "sourceType": "user" },
                "89306649/works_estimates": { "label": "default", "value": {}, "sourceType": "user" }
              }
            },
            "89306649/total_equity": {
              "label": "Total Equity",
              "value": "GBP 477,000.00",
              "sourceType": "calc",
              "freshness": "2026-07-30T07:21:56.979671+00:00",
              "sources": {
                "persons": { "label": "db", "sourceType": "user" },
                "89306649/status": { "label": "", "sourceType": "user" }
              }
            }
          }
        },
        "financial": {
          "label": "config",
          "value": {
            "current_home_sale_price": 0,
            "current_home_outstanding_mortgage": 0,
            "mortgage_rate": 0.0495,
            "mortgage_term_years": 27,
            "sinking_fund_rate": 0.01,
            "rental_income_monthly": 0,
            "life_insurance_monthly": 150,
            "working_weeks_per_year": 46
          },
          "sourceType": "user"
        }
      }
    },
    "89306649/yearly_sinking_fund": {
      "label": "Yearly Sinking Fund",
      "value": "GBP 8,000.00",
      "sourceType": "config",
      "freshness": "2026-07-30T07:21:56.980035+00:00",
      "sources": {
        "89306649/rightmove_price": { "label": "Rightmove", "value": "GBP 800,000.00", "sourceType": "user" },
        "financial": { "label": "config", "value": { "sinking_fund_rate": 0.01 }, "sourceType": "user" }
      }
    },
    "89306649/life_insurance_total": {
      "label": "Life Insurance Total",
      "value": "GBP 150.00",
      "sourceType": "calc",
      "freshness": "2026-07-30T07:21:56.979753+00:00",
      "sources": {
        "persons": { "label": "db", "sourceType": "user" }
      }
    },
    "89306649/rental_income": { "label": "default", "value": "GBP 0.00", "sourceType": "user" },
    "89306649/status": { "label": "", "sourceType": "user" },
    "financial": {
      "label": "config",
      "value": {
        "current_home_sale_price": 0, "current_home_outstanding_mortgage": 0,
        "mortgage_rate": 0.0495, "mortgage_term_years": 27,
        "sinking_fund_rate": 0.01, "rental_income_monthly": 0,
        "life_insurance_monthly": 150, "working_weeks_per_year": 46
      },
      "sourceType": "user"
    },
    "89306649/commute_breakdown": {
      "label": "commute_breakdown",
      "sourceType": "calc",
      "freshness": "2026-07-30T07:21:56.979416+00:00"
    },
    "89306649/council_tax": {
      "label": "Council Tax",
      "url": "https://www.gov.uk/council-tax-bands",
      "sourceType": "api",
      "freshness": "2026-07-30T07:21:56.960072+00:00"
    }
  }
}
```

### B: Council Tax Error — ambiguous address

```json
{
  "label": "Council Tax",
  "description": "Lookup failed: the property address '31 Isambard Road' matches multiple council tax records. We found Band D (£1,905/yr) and Band E (£2,329/yr) for this postcode.",
  "sourceType": "api",
  "freshness": "2026-07-30T07:21:56.960072+00:00",
  "url": "https://www.gov.uk/council-tax-bands",
  "status": "impossible",
  "error": "Ambiguous address: 2 council tax bands found for this postcode (D and E)",
  "sources": {
    "postcode": {
      "label": "postcode",
      "value": "UB2 4GN",
      "sourceType": "user",
      "freshness": "2026-07-30T09:15:00+00:00"
    },
    "address": {
      "label": "best_address",
      "value": "31 Isambard Road, Southall, UB2 4GN",
      "sourceType": "calc",
      "freshness": "2026-07-30T09:10:00+00:00"
    }
  }
}
```

### C: Commute Error — 409 from deep in the chain

```json
{
  "label": "commute_breakdown",
  "sourceType": "calc",
  "freshness": "2026-07-30T11:00:00+00:00",
  "sources": {
    "Simon/Office": {
      "label": "Simon/Office",
      "sourceType": "api",
      "freshness": "2026-07-30T10:30:00+00:00",
      "description": "Walking + train to Pimlico",
      "sources": {
        "walk": {
          "label": "walk",
          "value": "19 min",
          "sourceType": "api",
          "freshness": "2026-07-30T10:30:00+00:00"
        },
        "transit": {
          "label": "Southall → Ealing Broadway → Oxford Circus → Pimlico",
          "sourceType": "api",
          "freshness": "2026-07-30T10:30:00+00:00",
          "status": "impossible",
          "error": "TfL API returned 409 Conflict: route planner unavailable for this origin-destination pair during planned engineering works",
          "url": "https://api.tfl.gov.uk/"
        },
        "rail_fare": {
          "label": "rail_fare",
          "sourceType": "api",
          "freshness": "2026-07-30T10:35:00+00:00",
          "url": "https://www.nationalrail.co.uk/",
          "description": "National Rail monthly season ticket"
        }
      }
    }
  }
}
```

### D: EPC Rating — simple, clean

```json
{
  "label": "EPC Rating",
  "description": "Energy Performance Certificate from the official gov.uk register",
  "sourceType": "api",
  "freshness": "2026-07-22T10:15:00+00:00",
  "value": "Band C (68)",
  "url": "https://www.epcregister.com/",
  "sources": {
    "postcode": {
      "label": "postcode",
      "sourceType": "user",
      "freshness": "2026-07-30T09:15:00+00:00"
    },
    "best_address": {
      "label": "best_address",
      "sourceType": "calc",
      "sources": {
        "user_entered_address": {
          "label": "user_entered_address",
          "sourceType": "user",
          "freshness": "2026-07-29T14:00:00+00:00"
        },
        "rightmove_address": {
          "label": "rightmove_address",
          "sourceType": "user",
          "freshness": "2026-07-29T14:00:00+00:00"
        }
      }
    }
  }
}
```

---

## What a Good Solution Looks Like

Not a specific layout, but a verification: when you show your final screenshot to someone on the street who knows nothing about property and isn't confident with numbers, they must be able to:

- Point at £2,917/mo and say "that's the total of all the monthly costs added together"
- Tap into the mortgage and say "that's calculated from how much we borrow, the interest rate, and how long we take to pay it back"
- Tap further and say "how much we borrow is the house price plus tax plus renovations minus what we already have from selling our current home"
- See the council tax error and say "the address matched two different tax bands, so they couldn't calculate it"
- See "stamp duty" and either know what it is or find an explanation
- See that the Rightmove price is from yesterday but the EPC rating is 8 days old

If they can't do any of these, the design fails.
