import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ProvenanceToggle from '../ProvenanceToggle.vue'

const fakeProvenance = {
  label: 'Household Deposit',
  value: '£477,000.00',
  sourceType: 'calc' as const,
  formula: {
    lines: [{ label: 'Simon', value: '£550,000.00 sale − £373,000.00 mortgage + £0.00 cash = £177,000.00' }],
    result: '£477,000.00',
  },
}

describe('ProvenanceToggle — the single standard provenance affordance (P8)', () => {
  it('renders the standard trigger', () => {
    const wrapper = mount(ProvenanceToggle, { props: { provenance: fakeProvenance, title: 'Household Deposit' } })
    expect(wrapper.text()).toContain('How is this calculated?')
  })

  it('reveals the provenance on click', async () => {
    const wrapper = mount(ProvenanceToggle, { props: { provenance: fakeProvenance, title: 'Household Deposit' } })
    expect(wrapper.find('.provenance-toggle__body').exists()).toBe(false)
    await wrapper.find('button.provenance-toggle__trigger').trigger('click')
    expect(wrapper.find('.provenance-toggle__body').exists()).toBe(true)
    expect(wrapper.text()).toContain('£550,000.00 sale')
  })

  it('toggles aria-expanded and the label', async () => {
    const wrapper = mount(ProvenanceToggle, { props: { provenance: fakeProvenance } })
    const btn = wrapper.find('button.provenance-toggle__trigger')
    expect(btn.attributes('aria-expanded')).toBe('false')
    await btn.trigger('click')
    expect(btn.attributes('aria-expanded')).toBe('true')
    expect(btn.text()).toContain('Hide calculation')
  })

  it('renders an optional hint under the trigger', () => {
    const wrapper = mount(ProvenanceToggle, {
      props: { provenance: fakeProvenance, hint: 'The deposit reduces it — raise it in Settings.' },
    })
    expect(wrapper.text()).toContain('The deposit reduces it')
  })
})
