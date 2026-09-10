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

  it('lists each shared source once, linking to its full copy', () => {
    const w = mountView(totalMonthlyCost, { detailLevel: 'detail' })
    const index = w.findAll('a.shared-ref')
    expect(index.length, 'a shared source must appear in the index').toBeGreaterThan(0)
    const targets = index.map((a) => a.attributes('href') ?? '')
    expect(new Set(targets).size, 'the same source must not be listed twice').toBe(targets.length)
    for (const a of index) {
      const target = a.attributes('href')?.replace('#', '')
      expect(target && w.find(`[id="${target}"]`).exists(), `${a.text()} must link to a row in this view`).toBe(true)
    }
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

describe('ProvenanceView — shared nodes', () => {
  // The same node (same DAG id, `place`) feeds two consumers. Live symptom:
  // its children appeared NOWHERE — every occurrence was rendered as an
  // inert "📍 Shared" badge, so the subtree was unreachable.
  const sharedPlace: Provenance = {
    label: 'Place',
    sourceType: 'user',
    sources: { household: { label: 'Household members', sourceType: 'user' } },
  } as unknown as Provenance

  const tree = {
    label: 'Entry',
    sourceType: 'calc',
    sources: {
      walk: { label: 'Walk', sourceType: 'calc', sources: { place: sharedPlace } },
      drive: { label: 'Drive', sourceType: 'calc', sources: { place: sharedPlace } },
    },
  } as unknown as Provenance

  it('renders a shared node’s children at least once', () => {
    const w = mountView(tree, { detailLevel: 'detail' })
    expect(w.text()).toContain('Household members')
  })

  it('renders every later occurrence as a link to the full copy', () => {
    const w = mountView(tree, { detailLevel: 'detail' })
    const links = w.findAll('a.detail-node__ref-link')
    expect(links).toHaveLength(1)
    const target = links[0].attributes('href')?.replace('#', '')
    expect(target, 'the link must point at a row in this view').toBeTruthy()
    expect(w.find(`[id="${target}"]`).exists(), 'the target row must exist').toBe(true)
  })

  it('does not list the other uses on the full copy', () => {
    // The list read as a run-on of internal names ("used in 30 places: TfL
    // API TfL TfL Park & Ride Rail Fare…") and told the reader nothing they
    // act on.  The jump link is the affordance; this is the noise it lost.
    const w = mountView(tree, { detailLevel: 'detail' })
    const canonicalRow = w.findAll('.detail-node').find((row) => row.text().includes('Place'))
    expect(canonicalRow?.text()).not.toContain('used in')
    expect(w.findAll('.detail-node__uses')).toHaveLength(0)
  })

  it('links the index entry for a shared source to its full copy', () => {
    const w = mountView(tree, { detailLevel: 'detail' })
    const index = w.findAll('a.shared-ref')
    expect(index).toHaveLength(1)
    const target = index[0].attributes('href')?.replace('#', '')
    expect(target && w.find(`[id="${target}"]`).exists()).toBe(true)
  })

  it('points every one of a shared node’s other occurrences at the full copy', () => {
    const threePlaces = {
      label: 'Entry',
      sourceType: 'calc',
      sources: {
        walk: { label: 'Walk', sourceType: 'calc', sources: { place: sharedPlace } },
        drive: { label: 'Drive', sourceType: 'calc', sources: { place: sharedPlace } },
        transit: { label: 'Transit', sourceType: 'calc', sources: { place: sharedPlace } },
      },
    } as unknown as Provenance

    const w = mountView(threePlaces, { detailLevel: 'detail' })

    const links = w.findAll('a.detail-node__ref-link')
    expect(links, 'two of the three occurrences are references').toHaveLength(2)
    const targets = new Set(links.map((a) => a.attributes('href')))
    expect(targets.size, 'every reference points at the same full copy').toBe(1)
    const canonical = [...targets][0]?.replace('#', '')
    expect(canonical && w.find(`[id="${canonical}"]`).exists()).toBe(true)
    for (const link of links) {
      expect(link.attributes('title'), 'how widely it is shared belongs in the tooltip').toContain('3 places')
    }
  })

  it('renders a child that only a later occurrence carries', () => {
    // Same node id, two payload occurrences, different children (a stale or
    // partly-written tree).  Nothing may vanish: the full copy renders the
    // union of the children, once each.
    const withoutCoords = {
      label: 'Place',
      sourceType: 'user',
      sources: { household: { label: 'Household members', sourceType: 'user' } },
    } as unknown as Provenance
    const withCoords = {
      label: 'Place',
      sourceType: 'user',
      sources: {
        household: { label: 'Household members', sourceType: 'user' },
        coords: { label: 'Coordinates', sourceType: 'geocode' },
      },
    } as unknown as Provenance
    const splitTree = {
      label: 'Entry',
      sourceType: 'calc',
      sources: {
        walk: { label: 'Walk', sourceType: 'calc', sources: { place: withoutCoords } },
        drive: { label: 'Drive', sourceType: 'calc', sources: { place: withCoords } },
      },
    } as unknown as Provenance

    const w = mountView(splitTree, { detailLevel: 'detail' })

    const labels = w.findAll('.detail-node__label').map((el) => el.text())
    expect(labels, 'the child only the later occurrence carries must not vanish').toContain('Coordinates')
    expect(labels.filter((l) => l === 'Coordinates'), 'rendered once, at the full copy').toHaveLength(1)
    expect(labels.filter((l) => l === 'Household members')).toHaveLength(1)
  })

  it('keeps two different nodes that share a label apart', () => {
    // Identity is the node id, not the label: the with-bus and no-bus TfL
    // nodes are distinct calculations with the same name.
    const twoTfl = {
      label: 'Entry',
      sourceType: 'calc',
      sources: {
        tfl_no_bus: {
          label: 'TfL', sourceType: 'api',
          sources: { x: { label: 'No-bus detail', sourceType: 'api' } },
        },
        tfl_with_bus: {
          label: 'TfL', sourceType: 'api',
          sources: { y: { label: 'With-bus detail', sourceType: 'api' } },
        },
      },
    } as unknown as Provenance

    const w = mountView(twoTfl, { detailLevel: 'detail' })
    expect(w.text()).toContain('No-bus detail')
    expect(w.text()).toContain('With-bus detail')
    expect(w.findAll('a.detail-node__ref-link')).toHaveLength(0)
  })
})

describe('ProvenanceView — depth is visible however deep the tree goes', () => {
  // The indent classes stopped at 4, so every row deeper than that rendered
  // flush left and read as a top-level entry.  Live symptom (2026-09-10): the
  // commute total looked like "a ton of things at the top level" — 168 rows of
  // which the reader could only see four levels of hierarchy.
  function chain(depth: number): Provenance {
    let node = { label: `Leaf ${depth}`, sourceType: 'calc' } as unknown as Provenance
    for (let i = depth - 1; i >= 0; i--) {
      node = { label: `Level ${i}`, sourceType: 'calc', sources: { [`child_${i}`]: node } } as unknown as Provenance
    }
    return node
  }

  function indentPx(el: Element): number {
    const m = (el as HTMLElement).style.paddingLeft.match(/(\d+)px/)
    return m ? Number(m[1]) : 0
  }

  it('moves each level further in than the one above it', () => {
    const deep = chain(8)
    const w = mountView(deep, { detailLevel: 'detail' })

    const rows = w.findAll('.detail-node')
    expect(rows, 'eight levels of nesting plus the root').toHaveLength(9)

    const indents = rows.map((r) => indentPx(r.element))
    for (let i = 1; i < indents.length; i++) {
      expect(indents[i], `row ${i} sits no further in than row ${i - 1}`).toBeGreaterThan(indents[i - 1])
    }
  })

  it('says how deep each row is, for readers that cannot see the indent', () => {
    const w = mountView(chain(5), { detailLevel: 'detail' })
    const levels = w.findAll('.detail-node').map((r) => r.attributes('aria-level'))
    expect(levels).toEqual(['1', '2', '3', '4', '5', '6'])
  })
})
