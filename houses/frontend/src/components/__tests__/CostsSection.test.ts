import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import CostsSection from '../CostsSection.vue'

function mountCosts(overrides?: Record<string, unknown>) {
  return mount(CostsSection, {
    props: {
      affordability: {
        council_tax: { succeeded: false, value: null, error: null, provenance: {} },
        monthly_mortgage: { succeeded: false, value: null, error: null, provenance: {} },
        monthly_sinking_fund: { succeeded: false, value: null, error: null, provenance: {} },
        monthly_commute_cost: { succeeded: false, value: null, error: null, provenance: {} },
        total_monthly_housing_cost: { succeeded: false, value: null, error: null, provenance: {} },
        works_estimates: { succeeded: true, value: {}, error: null, provenance: {} },
        total_works: { succeeded: true, value: { amount: '0', currency: 'GBP' }, error: null, provenance: {} },
        ...(overrides?.affordability as Record<string, unknown> ?? {}),
      },
      epc: { succeeded: false, value: null, error: null, provenance: {} },
      persons: {
        succeeded: true,
        value: [
          { name: 'Simon', has_car: true, works_estimate_required: false },
          { name: 'Lorena', has_car: false, works_estimate_required: false },
          { name: 'Ashby', has_car: true, works_estimate_required: false },
        ],
        error: null,
        provenance: {},
      },
      rid: 'test123',
      ...overrides,
    },
  })
}

describe('CostsSection works estimates', () => {
  it('shows Cost of Works total row', () => {
    const wrapper = mountCosts()
    expect(wrapper.text()).toContain('Cost of Works')
    expect(wrapper.text()).toContain('£0')
  })

  it('shows per-person rows for all persons when dict is empty', () => {
    const wrapper = mountCosts()
    expect(wrapper.text()).toContain('Simon')
    expect(wrapper.text()).toContain('Lorena')
    expect(wrapper.text()).toContain('Ashby')
  })

  it('shows per-person value when dict has entry', () => {
    const wrapper = mountCosts({
      affordability: {
        works_estimates: { succeeded: true, value: { Ashby: 20000 }, error: null, provenance: {} },
        total_works: { succeeded: true, value: { amount: '20000', currency: 'GBP' }, error: null, provenance: {} },
      },
    })
    expect(wrapper.text()).toContain('£20,000')
  })

  it('shows £? for person without estimate', () => {
    const wrapper = mountCosts({
      affordability: {
        works_estimates: { succeeded: true, value: { Ashby: 20000 }, error: null, provenance: {} },
        total_works: { succeeded: true, value: { amount: '20000', currency: 'GBP' }, error: null, provenance: {} },
      },
    })
    // Simon has no estimate in the dict — should show "£?" or similar
    const simonRow = wrapper.text()
    expect(simonRow).toContain('Simon')
    // The value should indicate missing (not a number)
    expect(simonRow).toMatch(/Simon.*\?/)
  })

  it('opens inline editor on click', async () => {
    const wrapper = mountCosts({
      affordability: {
        works_estimates: { succeeded: true, value: { Ashby: 20000 }, error: null, provenance: {} },
        total_works: { succeeded: true, value: { amount: '20000', currency: 'GBP' }, error: null, provenance: {} },
      },
    })
    // Find an editable value and click it
    const valueEl = wrapper.find('.costs-value--clickable')
    expect(valueEl.exists()).toBe(true)
    await valueEl.trigger('click')
    // Should show an input
    expect(wrapper.find('input').exists()).toBe(true)
  })
})
