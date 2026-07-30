// Failing test: clicking rental income in CostsSection does nothing.

import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import CostsSection from '../CostsSection.vue'

vi.mock('../../services/api', () => ({
  patchWorksEstimate: vi.fn().mockResolvedValue(new Response()),
  patchRentalIncome: vi.fn().mockResolvedValue(new Response()),
}))

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
        rental_income: { succeeded: true, value: { amount: '500', currency: 'GBP' }, error: null, provenance: { label: 'user' } },
        ...(overrides?.affordability as Record<string, unknown> ?? {}),
      },
      epc: { succeeded: false, value: null, error: null, provenance: {} },
      persons: { succeeded: true, value: [], error: null, provenance: {} },
      rid: 'test123',
      currentPerson: 'Ashby',
      ...overrides,
    },
    global: { plugins: [createPinia()] },
  })
}

describe('CostsSection rental income click', () => {
  it('clicking rental income value starts editing when currentPerson is set', async () => {
    const wrapper = mountCosts()
    // Rental income should show the value
    expect(wrapper.text()).toContain('500')
    // Find ALL elements with text that includes '500' and has the editable class
    const editableSpans = wrapper.findAll('span').filter(s => {
      const text = s.text()
      return text.includes('500') && s.classes().includes('costs-value--editable')
    })
    expect(editableSpans.length).toBe(1)
    expect(editableSpans[0].attributes('class')).toContain('costs-value--editable')
    // Click it
    await editableSpans[0].trigger('click')
    // Now the input should appear
    const input = wrapper.find('input[type="number"]')
    expect(input.exists()).toBe(true)
  })
})
