# Provenance Redesign — Third Attempt: Designer Prompt

## Your Job

Design a **generic** provenance display that helps a non-technical house hunter understand where a number came from, how it was calculated, and whether to trust it. **Generic** means ONE component renders ALL fields — no custom visualizations per field type.

**Do not suggest new backend fields.** Use only the data that already exists in the provenance JSON (described below). The goal is to make the EXISTING data visible and comprehensible, not to design what COULD be with backend changes.

---

## Why Previous Attempts Failed

We've built two prototypes so far. Both had the same fatal flaw:

**They dropped information.** The old ProvenanceTree showed every node in the tree. Both prototypes summarised aggressively and lost the traceability that is the whole point of provenance. The user could no longer trace "£2,917/mo → mortgage → mortgage_required → property price + stamp duty + equity + works costs."

**Specific failures:**
1. Showed the mortgage result (£2,305) but dropped the sub-sources showing HOW it was calculated (interest rate 4.5%, 25-year term, £415k borrowed)
2. Rendered "financial_settings" as a card without showing the actual values inside it (mortgage_rate: 0.0495, term: 27 years)
3. Didn't surface what information was MISSING — nodes with empty labels, empty values, empty descriptions were silently hidden
4. Assumed new backend fields that don't exist instead of working with what's there
5. Treated "config" and "db" labels as useful when they're completely opaque to a user
6. Didn't handle the duplication problem: the same node appears under multiple parents (rightmove_price appears 3 times) but was either shown 3 times (confusing) or arbitrarily collapsed

---

## The Real Data You're Working With

Here is the actual provenance JSON for one property's total monthly cost. This is what the backend actually returns — nothing more, nothing less. Your design must work with THIS data structure.

```json
{
  "label": "Total Monthly Cost",
  "description": "89306649/total_monthly_cost: dep failed (Works estimate required for: Ashby; no council tax data)",
  "sourceType": "calc",
  "freshness": "2026-07-30T07:21:57.139579+00:00",
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
                "89306649/rightmove_price": {
                  "label": "Rightmove",
                  "value": "GBP 800,000.00", "sourceType": "user" },
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
        "89306649/rightmove_price": {
          "label": "Rightmove",
          "value": "GBP 800,000.00", "sourceType": "user" },
        "financial": {
          "label": "config",
          "value": { "sinking_fund_rate": 0.01 },
          "sourceType": "user"
        }
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
    "89306649/rental_income": {
      "label": "default",
      "value": "GBP 0.00",
      "sourceType": "user"
    },
    "89306649/status": { "label": "", "sourceType": "user" },
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

## Key Data Quality Problems You MUST Handle

This is NOT clean data. Your design must deal with these real-world issues:

1. **Empty labels**: Some nodes have `label: ""` (the `status` node). Your design can't crash or hide this — show SOMETHING.
2. **Error messages in description**: The `description` field contains "dep failed (Works estimate required for: Ashby)" — an internal error message, not a user-facing explanation.
3. **Missing values**: `commute_breakdown`, `council_tax`, `status` have no `value` field. `total_works` has no value either. Your design must still show them.
4. **Duplicate nodes in tree**: `rightmove_price` appears under `mortgage_required`, `stamp_duty`, AND `yearly_sinking_fund`. `financial` appears under `monthly_mortgage` AND as a top-level source. `persons` appears under `total_works`, `total_equity`, AND `life_insurance_total`. Showing all of these 3 times is noise. Hiding them loses information.
5. **Opaque internal names**: `"label": "db"`, `"label": "config"`, `"label": "default"` mean nothing to a user.
6. **Config blobs**: `financial.value` contains all settings in one blob — mortgage rate (4.95%), term (27 years), sinking fund rate (1%). These are individual inputs that affect the calculation but they're buried in a single value object.
7. **Missing calculation details**: `total_equity` has `value: "GBP 477,000.00"` but no formula showing HOW that number was reached. `life_insurance_total` has a value but no per-person breakdown.
8. **Inconsistent value formats**: Money is `"GBP 800,000.00"` (string), rates are `0.0495` (decimal number).
9. **Dead-end leaves**: `commute_breakdown` has `sourceType: "calc"` but no `value`, no `sources`, no formula. `council_tax` has a URL to gov.uk but no value.
10. **Mixed sourceType meaning**: `financial` has `sourceType: "user"` but it's user-configured settings, not user-entered data. `yearly_sinking_fund` has `sourceType: "config"` but it's calculated. The sourceType categories don't cleanly map to what users would expect.

---

## What Non-Technical Users Actually Need

A user looking at "£2,917.15/mo" wants to answer specific questions. Your design must let them answer ALL of them, at a glance or within one click:

- **"Is this right for me?"** — Do the settings reflect my situation? (my mortgage rate, my term, my commute)
- **"What's my biggest cost?"** — Which component dominates? (mortgage at 79%)
- **"How was this calculated?"** — Show the formula steps with the actual numbers that went in
- **"Where did the inputs come from?"** — Which values are from Rightmove, which from me, which from an API?
- **"How fresh is each input?"** — Is the property price from yesterday? Is the council tax from last month?
- **"What's a fact vs an estimate?"** — The property price is fixed. Renovation costs are a guess. The commute cost is calculated from live data.
- **"What assumptions were made?"** — Off-peak pricing? 27-year term? 2/3 share of sinking fund?
- **"Can I see the raw details?"** — For someone who wants to verify every step, show the full tree.

---

## Design Constraints

### Must Preserve
The old ProvenanceTree showed EVERY node in the tree with its:
- label
- sourceType (as color-coded badge)
- description
- value (if present)
- freshness (as colored dot)
- formula (as raw lines + result)
- url (as external link)
- Recursive children indented under parent

Your design must show ALL of this information too — you cannot drop any node or field. But you CAN present it differently.

### Must Be Generic
ONE component renders ANY provenance tree. No special handling for "this is a cost, show a bar" vs "this is a config, show settings." The component doesn't know what field it's rendering — it just gets a blob of provenance JSON.

### Must Handle Real Data Problems
- Empty labels → show a fallback
- Missing values → show the node exists, mark value as unknown
- Error descriptions → don't crash, flag as needing attention
- Duplicates → design a strategy (show once, reference elsewhere?)

### Duplicate Node Strategy
This is critical. The same `rightmove_price` and `financial` nodes appear in multiple branches. You have these options (pick one or invent better):
- **Option A**: Render duplicates in-place (like old tree). User sees the same thing 3 times. Honest but noisy.
- **Option B**: Detect duplicates, render the canonical copy once, show inline references elsewhere ("📎 Same as above"). Reduces noise, risks confusion.
- **Option C**: Flatten the tree into a list, group by node identity rather than tree structure. Different structural logic, harder to show the "flow."

Choose wisely and explain your reasoning.

---

## Deliverable

Write `tmp/provenanceview-v3.html` — a self-contained HTML/CSS/JS prototype showing one provenance card for the Total Monthly Cost scenario (the JSON above).

The prototype should:
1. Show the COMPLETE provenance in a usable layout
2. Handle ALL the data quality problems listed above
3. Use zero new backend fields — only what's in the JSON
4. Be generic — prove it by showing how the SAME layout would also render a different provenance (the commute breakdown or EPC rating) — you can include a second tab or inset
5. Include annotations explaining how each of the 10 data quality problems is handled
6. Annotate what information exists but was invisible or hard to find in the old tree

Do NOT show aspirational content that doesn't exist. If the data isn't there, show what IS there and note the gap.
