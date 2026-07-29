import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { usePropertiesStore } from '../../stores/properties'
import CostsSection from '../CostsSection.vue'
import * as api from '../../services/api'

vi.mock('../../services/api', () => ({
  patchWorksEstimate: vi.fn().mockResolvedValue(new Response()),
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
    const text = wrapper.text()
    expect(text).toContain('Simon')
    expect(text).toContain('Lorena')
    // Editable rows: Ashby's works + rental income (currentPerson is set)
    const editableEls = wrapper.findAll('.costs-value--editable')
    expect(editableEls.length).toBe(2)
    // Non-current persons should have readonly class
    const readonlyEls = wrapper.findAll('.costs-value--readonly')
    expect(readonlyEls.length).toBe(2)
  })

  it('refreshes detail after saving works estimate', async () => {
    const pinia = createPinia()
    const store = usePropertiesStore(pinia)
    const loadDetailSpy = vi.spyOn(store, 'loadDetail')

    const wrapper = mountCosts({}, pinia)

    const valueEl = wrapper.find('.costs-value--editable')
    await valueEl.trigger('click')

    const input = wrapper.find('input')
    await input.setValue('25000')
    await input.trigger('blur')

    expect(api.patchWorksEstimate).toHaveBeenCalledWith('test123', 'Ashby', 25000)
    expect(loadDetailSpy).toHaveBeenCalledWith('test123', true)
  })

  it('shows visual affordance on editable values', () => {
    const wrapper = mountCosts()
    const clickable = wrapper.find('.costs-value--editable')
    expect(clickable.exists()).toBe(true)
    expect(clickable.classes()).toContain('costs-value--editable')
  })
})
