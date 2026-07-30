import { createApp, h } from 'vue'
import ProvenanceView from '../../tmp/ProvenanceView.v4.vue'

// ── Dataset A: Total Monthly Cost (complex, errors, duplicates) ──
const datasetA = {
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

// ── Dataset B: Council Tax Error (ambiguous address) ──
const datasetB = {
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

// ── Dataset C: Commute Error (409 from deep in chain) ──
const datasetC = {
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

// ── Dataset D: EPC Rating (simple, clean) ──
const datasetD = {
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

const datasets = [
  { name: 'A: Total Monthly Cost', data: datasetA },
  { name: 'B: Council Tax Error', data: datasetB },
  { name: 'C: Commute Error', data: datasetC },
  { name: 'D: EPC Rating', data: datasetD },
]

// ── Render ──
const app = createApp({
  setup() {
    return () => h('div', { style: 'max-width: 400px; margin: 0 auto; background: var(--page-bg); min-height: 100vh;' },
      datasets.map(ds =>
        h('div', { key: ds.name, style: 'border-bottom: 2px solid var(--border); padding-bottom: 32px; margin-bottom: 32px;' },
          h('h2', { style: 'font-family: var(--font); font-size: 14px; font-weight: 600; color: var(--text-secondary); padding: 16px 16px 0; text-transform: uppercase; letter-spacing: 0.05em;' }, ds.name),
          h(ProvenanceView, { provenance: ds.data, showGlossary: ds.name.includes('EPC') })
        )
      )
    )
  }
})

app.mount('#app')
