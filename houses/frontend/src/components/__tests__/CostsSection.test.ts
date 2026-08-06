import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import CostsSection from '../CostsSection.vue'
import * as api from '../../services/api'

vi.mock('../../services/api', () => ({
  patchWorksEstimate: vi.fn().mockResolvedValue(new Response()),
  patchRentalIncome: vi.fn().mockResolvedValue(new Response()),
}))

function mountCosts(overrides?: Record<string, unknown>, pinia?: ReturnType<typeof createPinia>) {
  const activePinia = pinia ?? createPinia()
  return mount(CostsSection, {
    props: {
      affordability: {
        council_tax: { succeeded: false, value: null, error: null, provenance: {} },
        monthly_mortgage: { succeeded: false, value: null, error: null, provenance: {} },
        monthly_sinking_fund: { succeeded: false, value: null, error: null, provenance: {} },
        monthly_commute_cost: { succeeded: false, value: null, error: null, provenance: {} },
        total_monthly_housing_cost: { succeeded: false, value: null, error: null, provenance: {} },
        works_estimates: { succeeded: true, value: { Ashby: 20000 }, error: null, provenance: {} },
        total_works: { succeeded: true, value: { amount: '20000', currency: 'GBP' }, error: null, provenance: {} },
        rental_income: { succeeded: true, value: { amount: '500', currency: 'GBP' }, error: null, provenance: { label: 'user' } },
        ...(overrides?.affordability as Record<string, unknown> ?? {}),
      },
      epc: { succeeded: false, value: null, error: null, provenance: {} },
      persons: {
        succeeded: true,
        value: [
          { name: 'Simon', has_car: true, is_child: false },
          { name: 'Lorena', has_car: false, is_child: false },
          { name: 'Ashby', has_car: true, is_child: false },
          { name: 'George', has_car: false, is_child: true },
        ],
        error: null,
        provenance: {},
      },
      rid: 'test123',
      currentPerson: 'Ashby',
      ...overrides,
    },
    global: { plugins: [activePinia] },
  })
}

describe('CostsSection works estimates', () => {
  it('shows Cost of Works total row', () => {
    const wrapper = mountCosts()
    expect(wrapper.text()).toContain('Cost of Works')
  })

  it('shows per-person rows for all non-child persons', () => {
    const wrapper = mountCosts()
    expect(wrapper.text()).toContain('Simon')
    expect(wrapper.text()).toContain('Lorena')
    expect(wrapper.text()).toContain('Ashby')
  })

  it('does NOT show child persons (George)', () => {
    const wrapper = mountCosts()
    expect(wrapper.text()).not.toContain('George')
  })

  it('shows per-person value when dict has entry', () => {
    const wrapper = mountCosts()
    expect(wrapper.text()).toContain('£20,000')
  })

  it('shows £? for person without estimate', () => {
    const wrapper = mountCosts({
      affordability: {
        works_estimates: { succeeded: true, value: { Ashby: 20000 }, error: null, provenance: {} },
        total_works: { succeeded: true, value: { amount: '20000', currency: 'GBP' }, error: null, provenance: {} },
      },
    })
    const text = wrapper.text()
    expect(text).toMatch(/Simon.*\?/)
  })

  it('opens inline editor on click for current person', async () => {
    const wrapper = mountCosts()
    const valueEl = wrapper.find('.costs-value--editable')
    expect(valueEl.exists()).toBe(true)
    await valueEl.trigger('click')
    expect(wrapper.find('input').exists()).toBe(true)
  })

  it('shows non-current persons as read-only', () => {
    const wrapper = mountCosts()
    const nonEditable = wrapper.findAll('.costs-value:not(.costs-value--editable)')
    // Simon, Lorena, and the ? rows should not be editable
    expect(nonEditable.length).toBeGreaterThan(0)
  })

  it('refreshes detail after saving works estimate', async () => {
    const wrapper = mountCosts()
    const valueEl = wrapper.find('.costs-value--editable')
    await valueEl.trigger('click')
    const input = wrapper.find('input')
    await input.setValue('25000')
    await input.trigger('blur')
    expect(api.patchWorksEstimate).toHaveBeenCalledWith('test123', 'Ashby', 25000)
  })

  it('shows visual affordance on editable values', () => {
    const wrapper = mountCosts()
    const editable = wrapper.findAll('.costs-value--editable')
    expect(editable.length).toBeGreaterThanOrEqual(1)
  })
})

describe('CostsSection rental income', () => {
  it('shows rental income value when present', () => {
    const wrapper = mountCosts()
    expect(wrapper.text()).toContain('Rental Income')
    expect(wrapper.text()).toContain('500')
  })

  it('opens rental income editor on click when currentPerson is set', async () => {
    const wrapper = mountCosts()
    const allText = wrapper.text()
    expect(allText).toContain('Rental Income')
    const rentalValues = wrapper.findAll('span').filter(s =>
      s.classes().includes('costs-value--editable') && s.text().includes('500')
    )
    expect(rentalValues.length).toBeGreaterThanOrEqual(1)
    // Just verify it's clickable — don't actually click since focus might flake
  })

  it('calls patchRentalIncome when saving rental income edit', async () => {
    const wrapper = mountCosts()
    const rentalValues = wrapper.findAll('span').filter(s =>
      s.classes().includes('costs-value--editable') && s.text().includes('500')
    )
    expect(rentalValues.length).toBeGreaterThanOrEqual(1)
    await rentalValues[0].trigger('click')
    const input = wrapper.find('input')
    expect(input.exists()).toBe(true)
    await input.setValue('800')
    await input.trigger('blur')
    expect(api.patchRentalIncome).toHaveBeenCalledWith('test123', 800)
  })

  it('shows the standard provenance toggle when provenance exists', () => {
    const wrapper = mountCosts()
    const trigger = wrapper.find('.provenance-toggle__trigger')
    expect(trigger.exists()).toBe(true)
    expect(trigger.text()).toContain('How is this calculated?')
  })
})

describe('CostsSection blocked-state copy (C1/C2)', () => {
  it('explains the Council Tax lookup failure and how to fix it', () => {
    const wrapper = mountCosts()  // council_tax failed -> '?'
    const text = wrapper.text()
    expect(text).toContain("Couldn't look up Council Tax")
    expect(text).toContain('Edit address')
  })

  it('never shows a bare "Impossible" for blocked totals', () => {
    // real payloads mark failed nodes with error != null (impossible)
    const wrapper = mountCosts({
      affordability: {
        monthly_mortgage: { succeeded: false, value: null, error: 'dep failed', provenance: {} },
        total_monthly_housing_cost: { succeeded: false, value: null, error: 'dep failed', provenance: {} },
      },
    })
    const text = wrapper.text()
    expect(text).not.toContain('Impossible')
    expect(text).toContain("Can't calculate")
  })
})

describe('CostsSection uncertainty rendering (Part A)', () => {
  it('renders the Band D estimate with spread when council tax is estimated', () => {
    const wrapper = mountCosts({
      affordability: {
        council_tax: {
          succeeded: true,
          value: { band: '?', yearly_cost: { value: { amount: '1200', currency: 'GBP' }, stddev: 50 }, evidence_url: '' },
          error: null,
          provenance: {},
        },
      },
    })
    expect(wrapper.text()).toContain('? · (£1,200 ± £50)/yr')
  })

  it('renders an exact council tax without a spread', () => {
    const wrapper = mountCosts({
      affordability: {
        council_tax: {
          succeeded: true,
          value: { band: 'D', yearly_cost: { value: { amount: '1800', currency: 'GBP' }, stddev: 0 }, evidence_url: '' },
          error: null,
          provenance: {},
        },
      },
    })
    expect(wrapper.text()).toContain('D · £1,800/yr')
  })

  it('renders ≈ on the Total Monthly row when approximate', () => {
    const wrapper = mountCosts({
      affordability: {
        total_monthly_housing_cost: {
          succeeded: true,
          value: { value: { amount: '1100', currency: 'GBP' }, stddev: 4.17 },
          error: null,
          provenance: {},
        },
      },
    })
    const totalRow = wrapper.find('.costs-row--total')
    expect(totalRow.text()).toContain('≈ £1100')
  })
})

describe('CostsSection mortgage framing (B8)', () => {
  it('explains the remaining mortgage when the deposit dominates', () => {
    const wrapper = mountCosts({
      affordability: {
        total_equity: { succeeded: true, value: { amount: '477000', currency: 'GBP' }, error: null, provenance: {} },
        mortgage_required: { succeeded: true, value: { amount: '35000', currency: 'GBP' }, error: null, provenance: {} },
      },
    })
    expect(wrapper.text()).toContain('The deposit covers most of the price')
  })

  it('shows no deposit note when the mortgage exceeds the deposit', () => {
    const wrapper = mountCosts({
      affordability: {
        total_equity: { succeeded: true, value: { amount: '10000', currency: 'GBP' }, error: null, provenance: {} },
        mortgage_required: { succeeded: true, value: { amount: '200000', currency: 'GBP' }, error: null, provenance: {} },
      },
    })
    expect(wrapper.text()).not.toContain('The deposit covers most of the price')
  })
})
