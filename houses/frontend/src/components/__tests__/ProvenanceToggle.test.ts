import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { fetchPropertyProvenance } from '../../services/api'
import ProvenanceToggle from '../ProvenanceToggle.vue'

// On-demand contract (P8): the toggle never receives provenance — it
// loads the property's map through the real store on the open click.
const provApi = vi.hoisted(() => ({ maps: {} as Record<string, Record<string, unknown>> }))
vi.mock('../../services/api', () => ({
  fetchPropertyProvenance: vi.fn(async (rid: string) => provApi.maps[rid] ?? {}),
}))

const fakeProvenance = {
  label: 'Household Deposit',
  value: '£477,000.00',
  sourceType: 'calc' as const,
  formula: {
    lines: [{ label: 'Simon', value: '£550,000.00 sale − £373,000.00 mortgage + £0.00 cash = £177,000.00' }],
    result: '£477,000.00',
  },
}

function mountToggle(path = 'affordability.monthly_mortgage', rid = 'p1') {
  return mount(ProvenanceToggle, { props: { rid, path, title: 'Household Deposit' } })
}

beforeEach(() => {
  vi.clearAllMocks()
  provApi.maps = { p1: { 'affordability.monthly_mortgage': fakeProvenance } }
})

describe('ProvenanceToggle — the single standard provenance affordance (P8)', () => {
  it('renders an ICON trigger, and loads NOTHING until opened', () => {
    const wrapper = mountToggle()
    const btn = wrapper.find('button.provenance-toggle__trigger')
    expect(btn.find('.provenance-toggle__icon').text()).toBe('ⓘ')
    // The sentence must NOT be visible as a link — it lives in the
    // accessible name/tooltip only.
    expect(wrapper.text()).not.toContain('How is this calculated?')
    expect(btn.attributes('aria-label')).toBe('How is this calculated?')
    expect(fetchPropertyProvenance).not.toHaveBeenCalled()
  })

  it('fetches the provenance map only on the open click, then reveals the tree', async () => {
    const wrapper = mountToggle()
    expect(wrapper.find('.provenance-toggle__body').exists()).toBe(false)
    await wrapper.find('button.provenance-toggle__trigger').trigger('click')
    await flushPromises()
    expect(wrapper.find('.provenance-toggle__body').exists()).toBe(true)
    expect(wrapper.text()).toContain('£550,000.00 sale')
  })

  it('serves only the requested dotted path', async () => {
    const wrapper = mountToggle('affordability.monthly_mortgage')
    await wrapper.find('button.provenance-toggle__trigger').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Household Deposit')
    expect(wrapper.text()).not.toContain('Something Else')
  })

  it('toggles aria-expanded and the accessible name', async () => {
    const wrapper = mountToggle()
    const btn = wrapper.find('button.provenance-toggle__trigger')
    expect(btn.attributes('aria-expanded')).toBe('false')
    expect(btn.attributes('aria-label')).toBe('How is this calculated?')
    await btn.trigger('click')
    expect(btn.attributes('aria-expanded')).toBe('true')
    expect(btn.attributes('aria-label')).toBe('Hide calculation')
  })

  it('renders an optional hint under the trigger', () => {
    const wrapper = mount(ProvenanceToggle, {
      props: { rid: 'p1', path: 'affordability.monthly_mortgage', hint: 'The deposit reduces it — raise it in Settings.' },
    })
    expect(wrapper.text()).toContain('The deposit reduces it')
  })

  it('applies the per-surface transform to the served tree', async () => {
    const wrapper = mount(ProvenanceToggle, {
      props: {
        rid: 'p1',
        path: 'affordability.monthly_mortgage',
        transform: (p) => ({ ...p, label: 'Transformed' }),
      },
    })
    await wrapper.find('button.provenance-toggle__trigger').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Transformed')
  })
})