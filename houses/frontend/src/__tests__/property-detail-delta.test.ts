import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { createRouter, createWebHashHistory } from 'vue-router'
import { flushPromises, mount } from '@vue/test-utils'
import PropertyDetail from '../views/PropertyDetail.vue'
import { fetchPropertyDetail, fetchSettings } from '../services/api'

vi.mock('../services/api', () => ({
  fetchAllSummaries: vi.fn().mockResolvedValue({}),
  fetchPropertyDetail: vi.fn().mockResolvedValue(null),
  fetchSettings: vi.fn().mockResolvedValue({}),
  fetchWhatIfState: vi.fn().mockResolvedValue(false),
  patchTriage: vi.fn(),
  patchWorksEstimate: vi.fn().mockResolvedValue(new Response()),
  patchRentalIncome: vi.fn().mockResolvedValue(new Response()),
  removeProperty: vi.fn().mockResolvedValue({}),
}))

const detailFixture = {
  rid: '88275093',
  is_current_home: false,
  best_address: { succeeded: true, value: 'Moores Place, Hungerford', error: null },
  rightmove_price: { succeeded: true, value: { amount: '500000', currency: 'GBP' }, error: null },
  rightmove_bedrooms: { succeeded: true, value: 3, error: null },
  settings: { persons: { value: [] } },
  monthly_baseline: {
    rid: '88275093',
    address: '31 Isambard Road, Southall',
    couple: { value: '2383.36', approx: false },
    others: { value: '652.92', approx: false },
    others_rent_paid: 600,
  },
  affordability: {
    group_monthly_cost: {
      succeeded: true,
      value: {
        couple: { value: '2356.01', stddev: 0 },
        others: { value: '650.00', stddev: 0 },
        couple_label: 'S+L',
        couple_names: 'Simon+Lorena',
        others_label: 'A',
        delta_vs_home: {
          couple: { value: '-27.35', approx: false },
          others: { value: '-2.92', approx: false },
        },
      },
    },
  },
}

describe('PropertyDetail monthly figures', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(fetchPropertyDetail).mockResolvedValue(detailFixture as any)
    vi.mocked(fetchSettings).mockResolvedValue({})
  })

  async function mountDetail() {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = createRouter({
      history: createWebHashHistory(),
      routes: [{ path: '/property/:rid', component: PropertyDetail }, { path: '/', redirect: '/property/88275093' }],
    })
    await router.push('/property/88275093')
    await router.isReady()
    const wrapper = mount(PropertyDetail, { global: { plugins: [pinia, router] } })
    await flushPromises()
    return wrapper
  }

  it('shows the monthly DELTA vs the current home, like the index cards', async () => {
    const wrapper = await mountDetail()
    const text = wrapper.text()
    // THE CONTRACT: the detail page headline is the monthly increment
    // vs the current home (signed), matching the index cards — not the
    // absolute monthly total.
    expect(text).toContain('\u2212\u00a327')
    expect(text).not.toContain('£2,356')
  })

  it('the current home keeps its absolute totals', async () => {
    vi.mocked(fetchPropertyDetail).mockResolvedValue({
      ...detailFixture,
      is_current_home: true,
      affordability: {
        ...detailFixture.affordability,
        group_monthly_cost: {
          ...detailFixture.affordability.group_monthly_cost,
          value: { ...detailFixture.affordability.group_monthly_cost.value, delta_vs_home: null },
        },
      },
    } as any)
    const wrapper = await mountDetail()
    const text = wrapper.text()
    expect(text).toContain('£2,356')
  })
})
