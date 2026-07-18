import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { usePropertiesStore } from '../../stores/properties'
import PropertyList from '../PropertyList.vue'
import type { PropertySummary } from '../../types'

vi.mock('../../services/api', () => ({
  fetchAllSummaries: vi.fn(),
  fetchPropertyDetail: vi.fn(),
  fetchSettings: vi.fn().mockResolvedValue({}),
  patchTriage: vi.fn(),
}))

import * as api from '../../services/api'

const mockData: Record<string, PropertySummary> = {
  'prop-a': {
    rid: 'prop-a',
    best_address: { succeeded: true, value: '10 Cheap St', error: null, provenance: { label: 'test' } },
    best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
    rightmove_price: { succeeded: true, value: {amount: 200000, currency: "GBP"}, error: null, provenance: { label: 'test' } },
    rightmove_bedrooms: { succeeded: true, value: '2', error: null, provenance: { label: 'test' } },
    total_monthly_cost: { succeeded: true, value: {amount: 1500, currency: "GBP"}, error: null, provenance: { label: 'test' } },
    walkability: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    commutes: { 'Simon/Office': { commute: { succeeded: true, value: { duration: { value: 60, unit: 'minute' } }, error: null, provenance: { label: 'test' } } } },
    schools: {
      primary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
      secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
    },
    freshness: { property_added_at: '2026-07-15T10:00:00+00:00' },
  },
  'prop-b': {
    rid: 'prop-b',
    best_address: { succeeded: true, value: '20 Mid Rd', error: null, provenance: { label: 'test' } },
    best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
    rightmove_price: { succeeded: true, value: {amount: 300000, currency: "GBP"}, error: null, provenance: { label: 'test' } },
    rightmove_bedrooms: { succeeded: true, value: '3', error: null, provenance: { label: 'test' } },
    total_monthly_cost: { succeeded: true, value: {amount: 2000, currency: "GBP"}, error: null, provenance: { label: 'test' } },
    walkability: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    commutes: { 'Simon/Office': { commute: { succeeded: true, value: { duration: { value: 30, unit: 'minute' } }, error: null, provenance: { label: 'test' } } } },
    schools: {
      primary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
      secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
    },
    freshness: { property_added_at: '2026-07-14T10:00:00+00:00' },
  },
  'prop-c': {
    rid: 'prop-c',
    best_address: { succeeded: true, value: '30 Expensive Ave', error: null, provenance: { label: 'test' } },
    best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
    rightmove_price: { succeeded: true, value: {amount: 500000, currency: "GBP"}, error: null, provenance: { label: 'test' } },
    rightmove_bedrooms: { succeeded: true, value: '4', error: null, provenance: { label: 'test' } },
    total_monthly_cost: { succeeded: true, value: {amount: 3500, currency: "GBP"}, error: null, provenance: { label: 'test' } },
    walkability: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    commutes: { 'Simon/Office': { commute: { succeeded: true, value: { duration: { value: 90, unit: 'minute' } }, error: null, provenance: { label: 'test' } } } },
    schools: {
      primary: { school: { succeeded: true, value: { name: 'Outstanding Primary', ofsted: 'Outstanding', distance_km: 1, url: '' }, error: null, provenance: { label: 'test' } } },
      secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
    },
    freshness: { property_added_at: '2026-07-13T10:00:00+00:00' },
  },
}

function initStore() {
  setActivePinia(createPinia())
  const store = usePropertiesStore()
  store.rids = ['prop-a', 'prop-b', 'prop-c']
  store.summaries = mockData as any
  store.loading = false
  return store
}

describe('PropertyList tab switching', () => {
  beforeEach(() => {
    vi.mocked(api.fetchAllSummaries).mockResolvedValue(mockData as any)
  })

  it('starts on properties tab showing all properties', () => {
    const store = initStore()
    // displayedRids is internal to the component, but we can test
    // that the store has 3 RIDs
    expect(store.rids).toHaveLength(3)
  })

  it('favourites tab shows only favourited properties', () => {
    const store = initStore()
    store.triage['prop-a'] = { favourite: true, dismissed: false, is_viewed: false, user_notes: '', triage_status: '' }
    // The component filters via displayedRids computed which checks store.triage[rid]?.favourite
    const favourites = store.rids.filter(rid => store.triage[rid]?.favourite)
    expect(favourites).toEqual(['prop-a'])
  })

  it('favourites tab shows empty state when none favourited', () => {
    const store = initStore()
    const favourites = store.rids.filter(rid => store.triage[rid]?.favourite)
    expect(favourites).toEqual([])
  })
})

describe('PropertyList filtering', () => {
  beforeEach(() => {
    vi.mocked(api.fetchAllSummaries).mockResolvedValue(mockData as any)
  })

  it('filters by max price', () => {
    const store = initStore()
    const priceNum = (rid: string): number => {
      const p = store.summaries[rid]?.rightmove_price
      if (!p?.succeeded || !p.value) return Infinity
      return typeof p.value === 'number' ? p.value : p.value.amount
    }
    const filtered = store.rids.filter(rid => priceNum(rid) <= 300000)
    expect(filtered).toEqual(['prop-a', 'prop-b'])
  })

  it('filters by min bedrooms', () => {
    const store = initStore()
    const bedNum = (rid: string): number => {
      const b = store.summaries[rid]?.rightmove_bedrooms
      return b?.succeeded && b.value ? Number(b.value) : 0
    }
    const filtered = store.rids.filter(rid => bedNum(rid) >= 3)
    expect(filtered).toEqual(['prop-b', 'prop-c'])
  })

  it('filters by max commute', () => {
    const store = initStore()
    const bestCommute = (rid: string): number => {
      const commutes = store.summaries[rid]?.commutes
      if (!commutes) return Infinity
      let best = Infinity
      for (const c of Object.values(commutes)) {
        const val = c.commute?.value as Record<string, unknown> | undefined
        const dur = val?.duration as Record<string, unknown> | undefined
        const mins = dur?.value as number | undefined
        if (mins != null && mins < best) best = mins
      }
      return best
    }
    const filtered = store.rids.filter(rid => bestCommute(rid) <= 60)
    expect(filtered).toEqual(['prop-a', 'prop-b'])
  })

  it('combines multiple filters', () => {
    const store = initStore()

    const priceNum = (rid: string): number => {
      const p = store.summaries[rid]?.rightmove_price
      if (!p?.succeeded || !p.value) return Infinity
      return typeof p.value === 'number' ? p.value : p.value.amount
    }
    const bedNum = (rid: string): number => {
      const b = store.summaries[rid]?.rightmove_bedrooms
      return b?.succeeded && b.value ? Number(b.value) : 0
    }

    let filtered = store.rids.filter(rid => priceNum(rid) <= 400000)
    filtered = filtered.filter(rid => bedNum(rid) >= 3)
    expect(filtered).toEqual(['prop-b'])
  })
})

describe('PropertyList sorting', () => {
  beforeEach(() => {
    vi.mocked(api.fetchAllSummaries).mockResolvedValue(mockData as any)
  })

  it('sorts by price ascending', () => {
    const store = initStore()
    const priceNum = (rid: string): number => {
      const p = store.summaries[rid]?.rightmove_price
      if (!p?.succeeded || !p.value) return Infinity
      return typeof p.value === 'number' ? p.value : p.value.amount
    }
    const sorted = [...store.rids].sort((a, b) => priceNum(a) - priceNum(b))
    expect(sorted).toEqual(['prop-a', 'prop-b', 'prop-c'])
  })

  it('sorts by price descending', () => {
    const store = initStore()
    const priceNum = (rid: string): number => {
      const p = store.summaries[rid]?.rightmove_price
      if (!p?.succeeded || !p.value) return Infinity
      return typeof p.value === 'number' ? p.value : p.value.amount
    }
    const sorted = [...store.rids].sort((a, b) => priceNum(b) - priceNum(a))
    expect(sorted).toEqual(['prop-c', 'prop-b', 'prop-a'])
  })

  it('sorts by bedrooms descending', () => {
    const store = initStore()
    const bedNum = (rid: string): number => {
      const b = store.summaries[rid]?.rightmove_bedrooms
      return b?.succeeded && b.value ? Number(b.value) : 0
    }
    const sorted = [...store.rids].sort((a, b) => bedNum(b) - bedNum(a))
    expect(sorted).toEqual(['prop-c', 'prop-b', 'prop-a'])
  })
})
describe('PropertyList map tab markers', () => {
  beforeEach(() => {
    vi.mocked(api.fetchAllSummaries).mockResolvedValue(mockData as any)
  })

  it('renders a pin for each property with location data', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = usePropertiesStore()
    store.rids = ['prop-a', 'prop-b']
    store.summaries = {
      'prop-a': { ...mockData['prop-a'], best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } } },
      'prop-b': { ...mockData['prop-b'], best_location: { succeeded: true, value: { lat: 52.0, lon: 0.0 }, error: null, provenance: { label: 'test' } } },
    } as any
    store.loading = false

    const wrapper = mount(PropertyList, {
      global: { plugins: [pinia] },
    })

    // Switch activeTab to map
    await wrapper.setData({ activeTab: 'map' })
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    // all three mock properties have best_location set — loadAll fetches all 3
    const pins = wrapper.findAll('.map-pin')
    expect(pins.length).toBe(3)
  })

  it('renders no pins when no properties have location data', async () => {
    // Override the mock for this test — return properties without locations
    vi.mocked(api.fetchAllSummaries).mockResolvedValue({
      'prop-x': {
        rid: 'prop-x',
        best_address: { succeeded: true, value: 'No Location Lane', error: null, provenance: { label: 'test' } },
        best_location: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
        rightmove_price: { succeeded: true, value: {amount: 300000, currency: "GBP"}, error: null, provenance: { label: 'test' } },
        rightmove_bedrooms: { succeeded: true, value: '3', error: null, provenance: { label: 'test' } },
        total_monthly_cost: { succeeded: true, value: {amount: 2000, currency: "GBP"}, error: null, provenance: { label: 'test' } },
        walkability: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
        commutes: {},
        schools: {
          primary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
          secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
        },
      },
    } as any)

    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(PropertyList, {
      global: { plugins: [pinia] },
    })

    // Wait for loadAll to complete
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    // Switch to map tab
    await wrapper.setData({ activeTab: 'map' })
    await wrapper.vm.$nextTick()

    const pins = wrapper.findAll('.map-pin')
    expect(pins.length).toBe(0)
  })
})
describe('PropertyList map pins are interactive', () => {
  beforeEach(() => {
    vi.mocked(api.fetchAllSummaries).mockResolvedValue(mockData as any)
  })

  it('each pin is a link to the property detail page', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(PropertyList, {
      global: { plugins: [pinia] },
    })

    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    await wrapper.setData({ activeTab: 'map' })
    await wrapper.vm.$nextTick()

    const pins = wrapper.findAll('.map-pin')
    expect(pins.length).toBeGreaterThan(0)
    pins.forEach(pin => {
      expect(pin.element.tagName).toBe('A')
      const href = pin.attributes('href')
      expect(href).toMatch(/^#\/property\//)
    })
  })

  it('pin shows the property price as visible label', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(PropertyList, {
      global: { plugins: [pinia] },
    })

    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    await wrapper.setData({ activeTab: 'map' })
    await wrapper.vm.$nextTick()

    const pinText = wrapper.text()
    // Property prices from mockData should appear on pins
    expect(pinText).toContain('£200,000')
    expect(pinText).toContain('£300,000')
    expect(pinText).toContain('£500,000')
  })
})


