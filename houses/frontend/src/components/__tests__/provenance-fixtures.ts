import type { Provenance } from '../../types'

/**
 * The four provenance datasets the frontend design was built against.
 * These mirror houses/frontend/public/provenance-v4-data.js and the
 * tmp/provenance-v4-full-brief.md examples (A–D).
 *
 * A: Total Monthly Cost — complex chain, nested errors, duplicates, formulas
 * B: Council Tax Error — root-level error (status impossible)
 * C: Commute Error — error deep in the chain (TfL 409)
 * D: EPC Rating — simple clean success
 */

export const totalMonthlyCost: Provenance = {
  label: 'Total Monthly Cost',
  description: 'Total monthly housing cost = mortgage + sinking fund + insurance + commute + council tax − rental income',
  sourceType: 'calc',
  freshness: '2026-07-30T07:21:57.139579+00:00',
  expressionType: 'Add',
  formula: {
    lines: [
      { label: 'Mortgage', value: '£2,305.00', expression: 'PMT' },
      { label: 'Sinking Fund (yearly) ÷ 12 × ⅔', value: '£305.56', expression: 'Div, Mul' },
      { label: 'Life Insurance', value: '£23.00' },
      { label: 'Commute (yearly) ÷ 12', value: '£124.80', expression: 'Div' },
      { label: 'Council Tax (yearly) ÷ 12', value: '£158.79', expression: 'Div' },
      { label: 'Rental Income', value: '−£0.00', expression: 'Sub' },
    ],
    result: '£2,917.15/mo',
  },
  sources: {
    monthly_mortgage: {
      label: 'Monthly Mortgage',
      sourceType: 'calc',
      freshness: '2026-07-30T07:21:56.979960+00:00',
      expressionType: 'PMT',
      formula: {
        lines: [
          { label: 'Amount to borrow', value: '£415,000.00' },
          { label: 'Annual interest rate ÷ 12', value: '4.95% ÷ 12 = 0.4125%' },
          { label: 'Number of monthly payments', value: '27 × 12 = 324' },
        ],
        result: '£2,305.00/mo',
      },
      sources: {
        mortgage_required: {
          label: 'Mortgage Required',
          sourceType: 'calc',
          freshness: '2026-07-30T07:21:56.979845+00:00',
          expressionType: 'Add, Sub',
          formula: {
            lines: [
              { label: 'Property Price', value: '£800,000.00' },
              { label: 'Stamp Duty', value: '£27,500.00' },
              { label: 'Total Works', value: '£50,000.00' },
              { label: 'Total Equity', value: '−£477,000.00', expression: 'Sub' },
            ],
            result: '£415,000.00',
          },
          sources: {
            rightmove_price: {
              label: 'Rightmove',
              value: 'GBP 800,000.00',
              sourceType: 'user',
              freshness: '2026-07-30T09:15:00+00:00',
            },
            stamp_duty: {
              label: 'Stamp Duty',
              value: 'GBP 27,500.00',
              sourceType: 'calc',
              expressionType: 'TieredRate',
              freshness: '2026-07-30T07:21:56.979513+00:00',
              formula: {
                lines: [
                  { label: 'First £250,000 at 0%', value: '£0.00' },
                  { label: 'Next £550,000 at 5%', value: '£27,500.00' },
                ],
                result: '£27,500.00',
              },
              sources: {
                rightmove_price: {
                  label: 'Rightmove',
                  value: 'GBP 800,000.00',
                  sourceType: 'user',
                  freshness: '2026-07-30T09:15:00+00:00',
                },
              },
            },
            total_works: {
              label: 'Total Works',
              description: 'Missing estimate for Ashby — please enter renovation costs',
              sourceType: 'calc',
              status: 'impossible',
              error: 'Works estimate required for: Ashby',
              sources: {
                persons: { label: 'Household members', sourceType: 'user', freshness: '2026-07-30T07:00:00+00:00' },
                works_estimates: { label: 'Renovation estimates per person', value: {}, sourceType: 'user', freshness: '2026-07-30T09:15:00+00:00' },
              },
            },
            total_equity: {
              label: 'Total Equity',
              value: 'GBP 477,000.00',
              sourceType: 'calc',
              freshness: '2026-07-30T07:21:56.979671+00:00',
              description: 'Combined equity from all household members. Each person’s equity = their home sale price − outstanding mortgage + cash contribution.',
              sources: {
                persons: { label: 'Household members — 3 persons', sourceType: 'user', freshness: '2026-07-30T07:00:00+00:00' },
                ashby: {
                  label: 'Ashby’s equity',
                  sourceType: 'calc',
                  freshness: '2026-07-30T07:21:56+00:00',
                  description: '✨ Would require backend field: equity_breakdown_per_person',
                  formula: {
                    lines: [
                      { label: 'Home sale price (Ashby)', value: '£520,000', expression: '✨' },
                      { label: 'Outstanding mortgage', value: '−£82,000', expression: '✨' },
                      { label: 'Cash contribution', value: '£5,000', expression: '✨' },
                    ],
                    result: '£443,000',
                  },
                  sources: {
                    persons: { label: 'Ashby (you)', sourceType: 'user', freshness: '2026-07-30T07:00:00+00:00' },
                  },
                },
                simon: {
                  label: 'Simon’s equity',
                  sourceType: 'calc',
                  freshness: '2026-07-30T07:21:56+00:00',
                  description: '✨ Would require backend field: equity_breakdown_per_person',
                  formula: {
                    lines: [
                      { label: 'Home sale price (Simon)', value: '£0', expression: '✨' },
                      { label: 'Cash contribution', value: '£34,000', expression: '✨' },
                    ],
                    result: '£34,000',
                  },
                  sources: {
                    persons: { label: 'Simon', sourceType: 'user', freshness: '2026-07-30T07:00:00+00:00' },
                  },
                },
              },
            },
          },
        },
        mortgage_rate: {
          label: 'Mortgage Rate',
          value: 4.95,
          sourceType: 'user',
          freshness: '2026-07-30T07:00:00+00:00',
        },
        mortgage_term: {
          label: 'Mortgage Term (years)',
          value: 27,
          sourceType: 'user',
          freshness: '2026-07-30T07:00:00+00:00',
        },
      },
    },
    sinking_fund: {
      label: 'Yearly Sinking Fund',
      value: 'GBP 8,000.00',
      sourceType: 'calc',
      freshness: '2026-07-30T07:21:56.980035+00:00',
      expressionType: 'Mul',
      formula: {
        lines: [
          { label: 'Property Price', value: '£800,000.00' },
          { label: 'Sinking Fund Rate', value: '1.0%' },
        ],
        result: '£8,000.00/yr',
      },
      sources: {
        rightmove_price: { label: 'Rightmove', value: 'GBP 800,000.00', sourceType: 'user', freshness: '2026-07-30T09:15:00+00:00' },
        sinking_fund_rate: { label: 'Sinking Fund Rate', value: 1.0, sourceType: 'user', freshness: '2026-07-30T07:00:00+00:00' },
      },
    },
    life_insurance: {
      label: 'Life Insurance Total',
      value: 'GBP 150.00',
      sourceType: 'calc',
      freshness: '2026-07-30T07:21:56.979753+00:00',
      sources: {
        persons: { label: 'Household members', sourceType: 'user', freshness: '2026-07-30T07:00:00+00:00' },
        simon_life: { label: 'Simon’s life insurance', value: 'GBP 150.00', sourceType: 'user', freshness: '2026-07-30T07:00:00+00:00' },
        ashby_life: { label: 'Ashby’s life insurance', value: 'GBP 0.00', sourceType: 'user', freshness: '2026-07-30T07:00:00+00:00' },
        lorena_life: { label: 'Lorena’s life insurance', value: 'GBP 0.00', sourceType: 'user', freshness: '2026-07-30T07:00:00+00:00' },
      },
    },
    rental_income: {
      label: 'Rental Income',
      value: 'GBP 0.00',
      sourceType: 'user',
      freshness: '2026-07-30T09:15:00+00:00',
    },
    status: {
      label: 'Property Status',
      value: '',
      sourceType: 'user',
      freshness: '2026-07-30T09:15:00+00:00',
    },
    commute_breakdown: {
      label: 'Commute Breakdown',
      sourceType: 'calc',
      freshness: '2026-07-30T07:21:56.979416+00:00',
      description: 'Monthly commute cost aggregated from all household members',
      expressionType: 'Choose',
      formula: {
        lines: [
          { label: '✓ Simon/Office (transit selected)', value: '£92.40/mo' },
          { label: '✗ Simon/Office (drive)', value: '£124.80/mo' },
          { label: '✗ Simon/Office (walk)', value: 'too far' },
        ],
        result: '£124.80/mo',
      },
      sources: {
        simon: {
          label: 'Simon → Pimlico (transit selected)',
          sourceType: 'calc',
          value: 'GBP 92.40',
          sources: {
            walk: { label: 'Walk to Southall station', value: '19 min', sourceType: 'api' },
            transit: { label: 'TfL route (selected)', sourceType: 'api', url: 'https://api.tfl.gov.uk/' },
            rail_fare: { label: 'National Rail fare', sourceType: 'api', url: 'https://www.nationalrail.co.uk/' },
          },
        },
      },
    },
    council_tax: {
      label: 'Council Tax',
      sourceType: 'api',
      freshness: '2026-07-30T07:21:56.960072+00:00',
      url: 'https://www.gov.uk/council-tax-bands',
      value: 'GBP 159.00/mo (Band E)',
      sources: {
        postcode: { label: 'Postcode', value: 'UB2 4GN', sourceType: 'user', freshness: '2026-07-30T09:15:00+00:00' },
        best_address: { label: 'Property address', sourceType: 'calc' },
      },
    },
  },
}

export const councilTaxError: Provenance = {
  label: 'Council Tax',
  description: "Lookup failed: the property address '31 Isambard Road' matches multiple council tax records. We found Band D (£1,905/yr) and Band E (£2,329/yr) for this postcode.",
  sourceType: 'api',
  freshness: '2026-07-30T07:21:56.960072+00:00',
  url: 'https://www.gov.uk/council-tax-bands',
  status: 'impossible',
  error: 'Ambiguous address: 2 council tax bands found for this postcode (D and E)',
  sources: {
    postcode: { label: 'Postcode', value: 'UB2 4GN', sourceType: 'user', freshness: '2026-07-30T09:15:00+00:00' },
    best_address: {
      label: 'Property address',
      value: '31 Isambard Road, Southall, UB2 4GN',
      sourceType: 'calc',
      freshness: '2026-07-30T09:10:00+00:00',
      sources: {
        user_entered: { label: 'Address entered', sourceType: 'user', freshness: '2026-07-30T09:10:00+00:00' },
        rightmove_address: { label: 'Rightmove address', sourceType: 'user', freshness: '2026-07-30T09:00:00+00:00' },
      },
    },
  },
}

export const commuteError: Provenance = {
  label: 'Commute Breakdown',
  sourceType: 'calc',
  freshness: '2026-07-30T11:00:00+00:00',
  formula: {
    lines: [
      { label: '✗ Simon/Office', value: 'TfL API unavailable' },
      { label: '✗ Lorena/Aldgate', value: 'TfL API unavailable' },
    ],
    result: 'incomplete — transit data unavailable',
  },
  sources: {
    simon: {
      label: 'Simon → Pimlico',
      sourceType: 'api',
      freshness: '2026-07-30T10:30:00+00:00',
      description: 'Walking + train to Pimlico (TfL data unavailable)',
      sources: {
        walk: { label: 'Walk to Southall station', value: '19 min', sourceType: 'api', freshness: '2026-07-30T10:30:00+00:00' },
        transit: {
          label: 'Southall → Ealing Broadway → Oxford Circus → Pimlico',
          sourceType: 'api',
          freshness: '2026-07-30T10:30:00+00:00',
          status: 'impossible',
          error: 'TfL API returned 409 Conflict: route planner unavailable',
          url: 'https://api.tfl.gov.uk/',
        },
        rail_fare: {
          label: 'National Rail fare',
          sourceType: 'api',
          freshness: '2026-07-30T10:35:00+00:00',
          url: 'https://www.nationalrail.co.uk/',
          description: 'Monthly season ticket lookup',
        },
      },
    },
  },
}

export const epcRating: Provenance = {
  label: 'EPC Rating',
  description: 'Energy Performance Certificate from the official gov.uk register',
  sourceType: 'api',
  freshness: '2026-07-22T10:15:00+00:00',
  value: 'Band C (68)',
  url: 'https://www.epcregister.com/',
  sources: {
    postcode: { label: 'Postcode', sourceType: 'user', freshness: '2026-07-30T09:15:00+00:00' },
    best_address: {
      label: 'Property address',
      sourceType: 'calc',
      sources: {
        user_entered: { label: 'Address entered', sourceType: 'user', freshness: '2026-07-29T14:00:00+00:00' },
        rightmove_address: { label: 'Rightmove address', sourceType: 'user', freshness: '2026-07-29T14:00:00+00:00' },
      },
    },
  },
}

export const allDatasets: Record<string, Provenance> = {
  'Total Cost': totalMonthlyCost,
  'Council Tax Error': councilTaxError,
  'Commute Error': commuteError,
  'EPC Rating': epcRating,
}
