import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ProvenanceView from '../ProvenanceView.vue'
import type { Provenance } from '../../types'
import {
  totalMonthlyCost,
  councilTaxError,
  commuteError,
  epcRating,
} from './provenance-fixtures'

// Freeze "now" so freshness labels are deterministic.
// The fixtures use 2026-07-30 timestamps; freeze at 2026-07-31.
vi.useFakeTimers()
vi.setSystemTime(new Date('2026-07-31T08:00:00Z'))

function mountView(provenance: Provenance, opts: { detailLevel?: 'summary' | 'story' | 'detail' } = {}) {
  return mount(ProvenanceView, {
    props: {
      provenance,
      title: 'Test result',
      ...(opts.detailLevel ? { detailLevel: opts.detailLevel } : {}),
    },
  })
}

describe('ProvenanceView — Dataset A: Total Monthly Cost (complex)', () => {
  it('renders the root label and value', () => {
    const w = mountView(totalMonthlyCost)
    expect(w.text()).toContain('Total Monthly Cost')
    expect(w.text()).toContain('£2,917.15/mo')
  })

  it('renders the root description', () => {
    const w = mountView(totalMonthlyCost)
    expect(w.text()).toContain('Total monthly housing cost')
  })

  it('renders the root freshness', () => {
    const w = mountView(totalMonthlyCost)
    expect(w.text()).toMatch(/Updated today|Updated \d+ days? ago/)
  })

  it('renders the expression type badge for the root (Add)', () => {
    const w = mountView(totalMonthlyCost)
    expect(w.text()).toContain('Add')
  })

  it('renders all six formula lines with values', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'story' })
    const text = w.text()
    expect(text).toContain('Mortgage')
    expect(text).toContain('£2,305.00')
    expect(text).toContain('Sinking Fund (yearly) ÷ 12 × ⅔')
    expect(text).toContain('£305.56')
    expect(text).toContain('Life Insurance')
    expect(text).toContain('Commute (yearly) ÷ 12')
    expect(text).toContain('£124.80')
    expect(text).toContain('Council Tax (yearly) ÷ 12')
    expect(text).toContain('£158.79')
    expect(text).toContain('Rental Income')
    expect(text).toContain('−£0.00')
  })

  it('renders the formula result', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'story' })
    expect(w.text()).toContain('£2,917.15/mo')
  })

  it('renders expression annotations on formula lines', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'story' })
    const text = w.text()
    expect(text).toContain('PMT')
    expect(text).toContain('Sub')
  })

  it('shows source chips for every top-level source', () => {
    const w = mountView(totalMonthlyCost)
    const text = w.text()
    expect(text).toContain('Monthly Mortgage')
    expect(text).toContain('Yearly Sinking Fund')
    expect(text).toContain('Life Insurance')
    expect(text).toContain('Commute Breakdown')
    expect(text).toContain('Council Tax')
    expect(text).toContain('Rental Income')
  })

  it('renders a nested error (Total Works impossible) with its message', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'detail' })
    const text = w.text()
    expect(text).toContain('Total Works')
    expect(text).toContain('Works estimate required for: Ashby')
  })

  it('shows the full mortgage chain to leaf inputs', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'detail' })
    const text = w.text()
    expect(text).toContain('Monthly Mortgage')
    expect(text).toContain('Mortgage Required')
    expect(text).toContain('Rightmove')
    expect(text).toContain('Stamp Duty')
    expect(text).toContain('Total Equity')
  })

  it('shows per-person equity breakdown (Ashby and Simon)', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'detail' })
    const text = w.text()
    expect(text).toContain('Ashby’s equity')
    expect(text).toContain('Simon’s equity')
  })

  it('shows per-person life insurance breakdown', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'detail' })
    const text = w.text()
    expect(text).toContain('Simon’s life insurance')
    expect(text).toContain('Ashby’s life insurance')
    expect(text).toContain('Lorena’s life insurance')
  })

  it('shows expressional type badges for child calcs', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'detail' })
    const text = w.text()
    expect(text).toContain('PMT')
    expect(text).toContain('TieredRate')
    expect(text).toContain('Mul')
    expect(text).toContain('Choose')
  })

  it('renders user input values (Mortgage Rate, Term)', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'detail' })
    const text = w.text()
    expect(text).toContain('Mortgage Rate')
    expect(text).toContain('Mortgage Term (years)')
  })

  it('renders the council tax value with band', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'detail' })
    expect(w.text()).toContain('Band E')
  })

  it('renders API source URLs', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'detail' })
    expect(w.html()).toContain('https://www.gov.uk/council-tax-bands')
  })

  it('shows stale/aging indicators via per-node freshness', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'detail' })
    const text = w.text()
    expect(text).toMatch(/Updated \d+ days? ago/)
  })

  it('renders aspirational equity annotation', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'detail' })
    expect(w.text()).toContain('equity_breakdown_per_person')
  })

  it('renders rightmove price value', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'detail' })
    expect(w.text()).toContain('800,000')
  })
})

describe('ProvenanceView — Dataset B: Council Tax Error (root error)', () => {
  it('renders the label', () => {
    const w = mountView(councilTaxError)
    expect(w.text()).toContain('Council Tax')
  })

  it('shows the error status visibly', () => {
    const w = mountView(councilTaxError)
    const text = w.text()
    // Error message must be visible, not swallowed
    expect(text).toContain('Ambiguous address')
    expect(text).toContain('2 council tax bands')
  })

  it('shows the friendly description', () => {
    const w = mountView(councilTaxError)
    expect(w.text()).toContain('Lookup failed')
  })

  it('renders the source URL', () => {
    const w = mountView(councilTaxError)
    expect(w.html()).toContain('https://www.gov.uk/council-tax-bands')
  })

  it('shows the error is an error state (not presented as a value)', () => {
    const w = mountView(councilTaxError)
    expect(w.text()).toContain('Could not calculate')
  })

  it('shows the postcode input source', () => {
    const w = mountView(councilTaxError, { detailLevel: 'detail' })
    expect(w.text()).toContain('Postcode')
    expect(w.text()).toContain('UB2 4GN')
  })

  it('shows the best_address chain with its own sources', () => {
    const w = mountView(councilTaxError, { detailLevel: 'detail' })
    const text = w.text()
    expect(text).toContain('Property address')
    expect(text).toContain('Address entered')
    expect(text).toContain('Rightmove address')
  })
})

describe('ProvenanceView — Dataset C: Commute Error (deep error)', () => {
  it('renders the root label', () => {
    const w = mountView(commuteError)
    expect(w.text()).toContain('Commute Breakdown')
  })

  it('shows the incomplete formula result', () => {
    const w = mountView(commuteError, { detailLevel: 'story' })
    expect(w.text()).toContain('incomplete')
    expect(w.text()).toContain('transit data unavailable')
  })

  it('shows the failing formula lines (✗ Simon/Office)', () => {
    const w = mountView(commuteError, { detailLevel: 'story' })
    const text = w.text()
    expect(text).toContain('Simon/Office')
    expect(text).toContain('Lorena/Aldgate')
    expect(text).toContain('TfL API unavailable')
  })

  it('surfaces the deep transit error (409)', () => {
    const w = mountView(commuteError, { detailLevel: 'detail' })
    expect(w.text()).toContain('TfL API returned 409 Conflict')
  })

  it('shows the failing node label (transit route)', () => {
    const w = mountView(commuteError, { detailLevel: 'detail' })
    expect(w.text()).toContain('Southall → Ealing Broadway → Oxford Circus → Pimlico')
  })

  it('renders walk value (19 min)', () => {
    const w = mountView(commuteError, { detailLevel: 'detail' })
    expect(w.text()).toContain('19 min')
  })

  it('renders rail fare source URL', () => {
    const w = mountView(commuteError, { detailLevel: 'detail' })
    expect(w.html()).toContain('https://www.nationalrail.co.uk/')
  })
})

describe('ProvenanceView — Dataset D: EPC Rating (clean success)', () => {
  it('renders label and value', () => {
    const w = mountView(epcRating)
    expect(w.text()).toContain('EPC Rating')
    expect(w.text()).toContain('Band C (68)')
  })

  it('renders the description', () => {
    const w = mountView(epcRating)
    expect(w.text()).toContain('Energy Performance Certificate')
  })

  it('renders the source URL', () => {
    const w = mountView(epcRating)
    expect(w.html()).toContain('https://www.epcregister.com/')
  })

  it('renders freshness (8+ days old → aging)', () => {
    const w = mountView(epcRating)
    expect(w.text()).toMatch(/Updated \d+ days? ago/)
  })

  it('shows postcode and address sources', () => {
    const w = mountView(epcRating, { detailLevel: 'detail' })
    const text = w.text()
    expect(text).toContain('Postcode')
    expect(text).toContain('Property address')
  })

  it('does not show error text on a clean success', () => {
    const w = mountView(epcRating)
    expect(w.text()).not.toContain('impossible')
    expect(w.text()).not.toContain('Could not calculate')
  })
})

describe('ProvenanceView — cross-cutting', () => {
  it('shows a source count', () => {
    const w = mountView(totalMonthlyCost)
    expect(w.text()).toMatch(/data sources?/)
  })

  it('shows a calculation count', () => {
    const w = mountView(totalMonthlyCost)
    expect(w.text()).toMatch(/calculations?/)
  })

  it('shows the legend with source types', () => {
    const w = mountView(totalMonthlyCost)
    const text = w.text()
    expect(text).toContain('API')
    expect(text).toContain('Calculation')
    expect(text).toContain('Your input')
  })

  it('allows switching detail levels', async () => {
    const w = mountView(totalMonthlyCost)
    const buttons = w.findAll('.prov-view-toggle__btn')
    expect(buttons.length).toBeGreaterThanOrEqual(2)
    await buttons[1].trigger('click')
    expect(w.find('.story-flow').isVisible()).toBe(true)
  })

  it('deduplicates shared sources in the reference library', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'detail' })
    expect(w.text()).toContain('used in multiple places')
  })
})

describe('ProvenanceView — user-facing vs internal error fields', () => {
  it('renders the friendly leaf message, not the internal node-id chain', () => {
    // Simulates the serialized provenance from a 3-level dep failure:
    // the backend now puts the friendly message in error, and the raw
    // chain only in error_detail.
    const chainError: Provenance = {
      label: 'Total Monthly Cost',
      sourceType: 'calc',
      status: 'impossible',
      error: 'Works estimate required for: Ashby',
      description: 'Works estimate required for: Ashby',
    }
    const w = mountView(chainError)
    const text = w.text()
    expect(text).toContain('Works estimate required for: Ashby')
    expect(text).not.toContain('dep failed')
    expect(text).not.toContain('89306649')
    expect(text).not.toContain('mortgage_required')
  })

  it('shows Could not calculate title with the friendly reason', () => {
    const w = mountView({
      label: 'Council Tax',
      sourceType: 'api',
      status: 'impossible',
      error: 'Ambiguous address: 2 council tax bands found for this postcode',
    })
    expect(w.text()).toContain('Could not calculate')
    expect(w.text()).toContain('Ambiguous address')
  })

  it('does not render internal error_detail fields', () => {
    const w = mountView({
      label: 'Commute Breakdown',
      sourceType: 'calc',
      status: 'impossible',
      error: 'TfL API unavailable',
      description: 'TfL API unavailable',
    })
    expect(w.html()).not.toContain('error_detail')
    expect(w.html()).not.toContain('traceback')
    expect(w.html()).not.toContain('dep failed')
  })
})

describe('ProvenanceView — story view shows calc node sources', () => {
  it('sinking-fund style calc node shows its input sources in the story view', () => {
    // A calc node whose formula references inputs that are its own sources
    const sinkingFund: Provenance = {
      label: 'Yearly Sinking Fund',
      sourceType: 'calc',
      value: 'GBP 8,000.00',
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
    }
    const w = mountView(sinkingFund, { detailLevel: 'story' })
    const text = w.text()
    // Formula shown
    expect(text).toContain('Property Price')
    expect(text).toContain('£800,000.00')
    // The input sources must be visible as cards, not just formula lines
    expect(text).toContain('Rightmove')
    expect(text).toContain('Sinking Fund Rate')
  })
})

describe('ProvenanceView — nested calc node sources in story view', () => {
  it('shows the sources of a nested calc node when the calc has its own inputs', async () => {
    // Total Cost root → sinking_fund calc → its own inputs (rightmove, rate)
    const totalCostWithSinking: Provenance = {
      label: 'Total Monthly Cost',
      sourceType: 'calc',
      value: 'GBP 2917.15',
      formula: {
        lines: [{ label: 'Sinking Fund (yearly) ÷ 12 × ⅔', value: '£305.56' }],
        result: '£2,917.15/mo',
      },
      sources: {
        sinking_fund: {
          label: 'Yearly Sinking Fund',
          sourceType: 'calc',
          value: 'GBP 8,000.00',
          formula: {
            lines: [
              { label: 'Property Price', value: '£800,000.00' },
              { label: 'Sinking Fund Rate', value: '1.0%' },
            ],
            result: '£8,000.00/yr',
          },
          sources: {
            rightmove_price: { label: 'Rightmove', value: 'GBP 800,000.00', sourceType: 'user' },
            sinking_fund_rate: { label: 'Sinking Fund Rate', value: 1.0, sourceType: 'user' },
          },
        },
      },
    }
    const w = mountView(totalCostWithSinking, { detailLevel: 'story' })
    const text = w.text()
    // The nested calc card is shown, with an expand affordance
    expect(text).toContain('Yearly Sinking Fund')
    // Before expanding: the formula and its inputs are NOT yet visible
    expect(text).not.toContain('Property Price')
    // Click the calc card to expand it (as a user would)
    const calcCard = w.findAll('.flow-card__body--clickable').find(b => b.text().includes('Yearly Sinking Fund'))
    expect(calcCard).toBeTruthy()
    await calcCard!.trigger('click')
    await w.vm.$nextTick()
    const expanded = w.text()
    // Its own formula is shown
    expect(expanded).toContain('Property Price')
    expect(expanded).toContain('Sinking Fund Rate')
    // Its input sources are reachable — the Rightmove source card exists
    expect(expanded).toContain('Rightmove')
    expect(expanded).toContain('£800,000.00')
  })
})

describe('ProvenanceView — value formatting', () => {
  it('formats plain-object values as Name: value pairs, never [object Object]', () => {
    const worksEstimates: Provenance = {
      label: 'Renovation estimates',
      sourceType: 'user',
      value: { Ashby: 20000 },
    }
    const w = mountView(worksEstimates, { detailLevel: 'story' })
    expect(w.html()).not.toContain('[object Object]')
    expect(w.text()).toContain('Ashby')
    expect(w.text()).toContain('20000')
  })

  it('formats arrays of objects as names, never [object Object]', () => {
    const persons: Provenance = {
      label: 'Household members',
      sourceType: 'user',
      value: [
        { name: 'Simon', has_car: true, is_child: false, places: [] },
        { name: 'Lorena', has_car: false, is_child: false, places: [] },
        { name: 'George', has_car: false, is_child: true, places: [] },
      ],
    }
    const w = mountView(persons, { detailLevel: 'story' })
    expect(w.html()).not.toContain('[object Object]')
    expect(w.text()).toContain('Simon')
    expect(w.text()).toContain('Lorena')
    expect(w.text()).toContain('George')
  })

  it('formats nested object values recursively', () => {
    const commute: Provenance = {
      label: 'Commute Breakdown',
      sourceType: 'calc',
      value: { persons: { Simon: { daily_gbp: 12.5 } } },
    }
    const w = mountView(commute, { detailLevel: 'story' })
    expect(w.html()).not.toContain('[object Object]')
    expect(w.text()).toContain('Simon')
    expect(w.text()).toContain('12.5')
  })
})
