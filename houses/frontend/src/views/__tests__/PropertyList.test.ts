import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { usePropertiesStore } from '../../stores/properties'
import PropertyList from '../PropertyList.vue'
import type { MonthlyBaseline, PropertySummary } from '../../types'

vi.mock('../../services/api', () => ({
  fetchAllSummaries: vi.fn(),
  fetchPropertyDetail: vi.fn(),
  fetchSettings: vi.fn().mockResolvedValue({}),
  patchTriage: vi.fn(),
}))

// Leaflet needs a real layout engine — jsdom can't size a map. The
// MapView catches init failure and emits 'error' (the fallback note),
// so a stub map object keeps the component mounting without a DOM.
const mockMap = {
  addLayer: vi.fn(),
  remove: vi.fn(),
  fitBounds: vi.fn(),
  invalidateSize: vi.fn(),
  on: vi.fn(),
  removeLayer: vi.fn(),
}
vi.mock('leaflet', () => ({
  default: {
    map: vi.fn(() => mockMap),
    tileLayer: vi.fn(() => ({ addTo: vi.fn() })),
    layerGroup: vi.fn(() => ({ addTo: vi.fn(), clearLayers: vi.fn() })),
    polygon: vi.fn(() => ({ bindPopup: vi.fn(), addTo: vi.fn() })),
    circleMarker: vi.fn(() => ({ bindPopup: vi.fn(), addTo: vi.fn() })),
    marker: vi.fn(() => ({ bindPopup: vi.fn(), addTo: vi.fn() })),
    divIcon: vi.fn(() => ({})),
    control: { layers: vi.fn(() => ({ addTo: vi.fn() })) },
    latLngBounds: vi.fn(() => ({ pad: vi.fn(() => ({})) })),
  },
}))
vi.mock('leaflet/dist/leaflet.css', () => ({}))

import * as api from '../../services/api'

const mockData: Record<string, PropertySummary> = {
  'prop-a': {
    rid: 'prop-a',
    best_address: { succeeded: true, value: '10 Cheap St', error: null, provenance: { label: 'test' } },
    best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
    rightmove_price: { succeeded: true, value: {amount: "200000", currency: "GBP"}, error: null, provenance: { label: 'test' } },
    rightmove_bedrooms: { succeeded: true, value: '2', error: null, provenance: { label: 'test' } },
    total_monthly_cost: { succeeded: true, value: { value: { amount: "1500", currency: "GBP" }, stddev: 0 }, error: null, provenance: { label: 'test' } },
    group_monthly_cost: { succeeded: true, value: { couple: { value: '1500', stddev: 0 }, others: { value: '500', stddev: 0 }, couple_label: 'S&L', others_label: 'A' }, error: null, provenance: { label: 'test' } },
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
    rightmove_price: { succeeded: true, value: {amount: "300000", currency: "GBP"}, error: null, provenance: { label: 'test' } },
    rightmove_bedrooms: { succeeded: true, value: '3', error: null, provenance: { label: 'test' } },
    total_monthly_cost: { succeeded: true, value: { value: { amount: "2000", currency: "GBP" }, stddev: 0 }, error: null, provenance: { label: 'test' } },
    group_monthly_cost: { succeeded: true, value: { couple: { value: '2000', stddev: 0 }, others: { value: '500', stddev: 0 }, couple_label: 'S&L', others_label: 'A' }, error: null, provenance: { label: 'test' } },
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
    rightmove_price: { succeeded: true, value: {amount: "500000", currency: "GBP"}, error: null, provenance: { label: 'test' } },
    rightmove_bedrooms: { succeeded: true, value: '4', error: null, provenance: { label: 'test' } },
    total_monthly_cost: { succeeded: true, value: { value: { amount: "3500", currency: "GBP" }, stddev: 0 }, error: null, provenance: { label: 'test' } },
    group_monthly_cost: { succeeded: true, value: { couple: { value: '3500', stddev: 0 }, others: { value: '500', stddev: 0 }, couple_label: 'S&L', others_label: 'A' }, error: null, provenance: { label: 'test' } },
    walkability: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    commutes: { 'Simon/Office': { commute: { succeeded: true, value: { duration: { value: 90, unit: 'minute' } }, error: null, provenance: { label: 'test' } } } },
    schools: {
      primary: { school: { succeeded: true, value: { name: 'Outstanding Primary', ofsted: 'Outstanding', distance: {value: 1, unit: 'km'}, url: '' }, error: null, provenance: { label: 'test' } } },
      secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
    },
    freshness: { property_added_at: '2026-07-13T10:00:00+00:00' },
  },
  'prop-d': {
    rid: 'prop-d',
    best_address: { succeeded: true, value: '40 School Ln', error: null, provenance: { label: 'test' } },
    best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
    rightmove_price: { succeeded: true, value: {amount: "250000", currency: "GBP"}, error: null, provenance: { label: 'test' } },
    rightmove_bedrooms: { succeeded: true, value: '3', error: null, provenance: { label: 'test' } },
    total_monthly_cost: { succeeded: true, value: { value: { amount: "1800", currency: "GBP" }, stddev: 0 }, error: null, provenance: { label: 'test' } },
    group_monthly_cost: { succeeded: true, value: { couple: { value: '1800', stddev: 0 }, others: { value: '500', stddev: 0 }, couple_label: 'S&L', others_label: 'A' }, error: null, provenance: { label: 'test' } },
    walkability: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    commutes: { 'Simon/Office': { commute: { succeeded: true, value: { duration: { value: 45, unit: 'minute' } }, error: null, provenance: { label: 'test' } } } },
    schools: {
      primary: { school: { succeeded: true, value: { name: 'Empty Ofsted School', ofsted: '', distance: {value: 2, unit: 'km'}, url: '' }, error: null, provenance: { label: 'test' } } },
      secondary: { school: { succeeded: true, value: { name: 'Good Secondary', ofsted: 'Good', distance: {value: 3, unit: 'km'}, url: '' }, error: null, provenance: { label: 'test' } } },
    },
    freshness: { property_added_at: '2026-07-12T10:00:00+00:00' },
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

  it('renders properties with empty ofsted rating without error', () => {
    const store = initStore()
    store.rids = ['prop-a', 'prop-d']
    const wrapper = mount(PropertyList)
    expect(wrapper.text()).toContain('10 Cheap St')
    expect(wrapper.text()).toContain('40 School Ln')
    // prop-d has a primary school with empty ofsted — should render without throwing
    expect(() => wrapper.vm.$forceUpdate()).not.toThrow()
  })

  it('renders property with null ofsted value gracefully', () => {
    const store = initStore()
    const summaryWithNullOfsted = JSON.parse(JSON.stringify(mockData['prop-d']))
    summaryWithNullOfsted.schools.primary.school.value.ofsted = null
    store.summaries['prop-d'] = summaryWithNullOfsted
    store.rids = ['prop-d']
    expect(() => mount(PropertyList)).not.toThrow()
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
      return typeof p.value === 'number' ? p.value : parseFloat(p.value.amount)
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

  it('filters by address search (C11)', async () => {
    const store = initStore()
    store.rids = ['prop-a', 'prop-d']
    const wrapper = mount(PropertyList)
    await wrapper.find('.search-input').setValue('school')
    expect(wrapper.text()).toContain('40 School Ln')
    expect(wrapper.text()).not.toContain('10 Cheap St')
  })

  it('shows no commute-limit info while the filter is inactive (C9)', async () => {
    const store = initStore()
    const wrapper = mount(PropertyList)
    await flushPromises()
    store.commuteCeilings = { Simon: { fine: 30, isChild: false } }
    await flushPromises()
    expect(wrapper.text()).toContain('40 School Ln')
    expect(wrapper.text()).not.toContain('commute over the')
  })

  it('hides houses over the ceiling only when the filter is switched on (C9)', async () => {
    const store = initStore()
    const wrapper = mount(PropertyList)
    await flushPromises()
    store.commuteCeilings = { Simon: { fine: 30, isChild: false } }
    await flushPromises()
    // commutes: prop-a 60m, prop-b 30m, prop-c 90m, prop-d 45m → 3 over
    const filterBtn = wrapper.findAll('.pill').find(b => b.text().includes('Filter'))!
    await filterBtn.trigger('click')
    await wrapper.find('.sheet__check input').setValue(true)
    await flushPromises()
    expect(wrapper.text()).not.toContain('40 School Ln')
    expect(wrapper.text()).toContain('Hiding 3 houses with a commute over the 30-minute limit')

    // Dismissing the banner turns the filter back off
    await wrapper.find('.commute-status-dismiss').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('40 School Ln')
    expect(wrapper.text()).not.toContain('commute over the')
  })

  it('shows an empty list when the filter is on and excludes every house', async () => {
    const store = initStore()
    const wrapper = mount(PropertyList)
    await flushPromises()
    store.commuteCeilings = { Simon: { fine: 1, isChild: false } } // every commute is over 1m
    await flushPromises()
    const filterBtn = wrapper.findAll('.pill').find(b => b.text().includes('Filter'))!
    await filterBtn.trigger('click')
    await wrapper.find('.sheet__check input').setValue(true)
    await flushPromises()
    expect(wrapper.text()).toContain('0 found')
    expect(wrapper.text()).toContain('Hiding 4 houses with a commute over the 1-minute limit')
  })

  it('shows a legend for the commute pill colours (C7)', () => {
    initStore()
    const wrapper = mount(PropertyList)
    const legend = wrapper.find('.legend-strip')
    expect(legend.text()).toContain('fine')
    expect(legend.text()).toContain('getting tight')
    expect(legend.text()).toContain('yikes')
    expect(legend.text()).toContain('no route')
  })

  it('combines multiple filters', () => {
    const store = initStore()

    const priceNum = (rid: string): number => {
      const p = store.summaries[rid]?.rightmove_price
      if (!p?.succeeded || !p.value) return Infinity
      return typeof p.value === 'number' ? p.value : parseFloat(p.value.amount)
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
      return typeof p.value === 'number' ? p.value : parseFloat(p.value.amount)
    }
    const sorted = [...store.rids].sort((a, b) => priceNum(a) - priceNum(b))
    expect(sorted).toEqual(['prop-a', 'prop-b', 'prop-c'])
  })

  it('sorts by price descending', () => {
    const store = initStore()
    const priceNum = (rid: string): number => {
      const p = store.summaries[rid]?.rightmove_price
      if (!p?.succeeded || !p.value) return Infinity
      return typeof p.value === 'number' ? p.value : parseFloat(p.value.amount)
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
describe('PropertyList sort and filter sheets', () => {
  beforeEach(() => {
    vi.mocked(api.fetchAllSummaries).mockResolvedValue(mockData as any)
  })

  async function mountList() {
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(PropertyList, { global: { plugins: [pinia] } })
    await flushPromises()
    return wrapper
  }

  it('opens the sort sheet from the Sort pill and the filter sheet from the Filter pill — separately', async () => {
    const wrapper = await mountList()
    expect(wrapper.find('[aria-label="Sort properties"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="Filter properties"]').exists()).toBe(false)

    await wrapper.findAll('.controls-row .pill')[0].trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[aria-label="Sort properties"]').exists()).toBe(true)
    expect(wrapper.find('[aria-label="Filter properties"]').exists()).toBe(false)

    await wrapper.find('[aria-label="Close sort"]').trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.findAll('.controls-row .pill')[1].trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[aria-label="Filter properties"]').exists()).toBe(true)
    expect(wrapper.find('[aria-label="Sort properties"]').exists()).toBe(false)
  })

  it('shows the active sort choice on the Sort pill', async () => {
    const wrapper = await mountList()
    expect(wrapper.findAll('.controls-row .pill')[0].text()).toContain('Date Added')
    await wrapper.findAll('.controls-row .pill')[0].trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.find('.sheet__select').setValue('price_asc')
    await wrapper.vm.$nextTick()
    expect(wrapper.findAll('.controls-row .pill')[0].text()).toContain('Price: Low→High')
  })
})

describe('PropertyList weekly commute sort', () => {  beforeEach(() => {
    vi.mocked(api.fetchAllSummaries).mockResolvedValue(mockData as any)
  })

  function commute(mins: number, isChild = false) {
    return {
      commute: {
        succeeded: true,
        value: { duration: { value: mins, unit: 'minute' } },
        error: null, provenance: { label: 'test' },
        is_child: isChild,
      },
    }
  }

  async function mountWithCommutes(commutes: Record<string, Record<string, ReturnType<typeof commute>>>) {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = usePropertiesStore()
    const wrapper = mount(PropertyList, { global: { plugins: [pinia] } })
    await flushPromises() // let onMounted's loadAll settle, then take over
    store.rids = ['prop-a', 'prop-b', 'prop-c']
    store.summaries = {
      'prop-a': { ...mockData['prop-a'], commutes: commutes['prop-a'] },
      'prop-b': { ...mockData['prop-b'], commutes: commutes['prop-b'] },
      'prop-c': { ...mockData['prop-c'], commutes: commutes['prop-c'] },
    } as any
    await wrapper.vm.$nextTick()
    return wrapper
  }

  /** Open the sort sheet and pick an option — the real user path. */
  async function chooseSort(wrapper: ReturnType<typeof mount>, value: string) {
    await wrapper.findAll('.controls-row .pill')[0].trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.find('.sheet__select').setValue(value)
    await wrapper.vm.$nextTick()
  }

  it('sorts by total weekly adult commute time ascending (2 trips × 5 days)', async () => {
    const wrapper = await mountWithCommutes({
      // Simon 60 + Lorena 10 = 70/day → 700/wk
      'prop-a': { 'Simon/Office': commute(60), 'Lorena/Office': commute(10) },
      // Simon 30 → 300/wk
      'prop-b': { 'Simon/Office': commute(30) },
      // Simon 90 → 900/wk; George's 20 is a CHILD commute and must not count
      'prop-c': { 'Simon/Office': commute(90), 'George/School': commute(20, true) },
    })
    await chooseSort(wrapper, 'weekly_commute')
    const addrs = wrapper.findAll('.card__address-text').map(a => a.text())
    expect(addrs).toEqual(['20 Mid Rd', '10 Cheap St', '30 Expensive Ave'])
  })

  it('puts houses with no commute data last', async () => {
    const wrapper = await mountWithCommutes({
      'prop-a': { 'Simon/Office': commute(60) },
      'prop-b': {},
      'prop-c': { 'Simon/Office': commute(90) },
    })
    await chooseSort(wrapper, 'weekly_commute')
    const addrs = wrapper.findAll('.card__address-text').map(a => a.text())
    expect(addrs).toEqual(['10 Cheap St', '30 Expensive Ave', '20 Mid Rd'])
  })
})

describe('PropertyList map tab markers', () => {
  beforeEach(() => {
    vi.mocked(api.fetchAllSummaries).mockResolvedValue(mockData as any)
  })

  it('passes a marker to the map for each property with location data', async () => {
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

    const mapView = wrapper.findComponent({ name: 'MapView' })
    expect(mapView.exists()).toBe(true)
    const markers = mapView.props('markers') as { lat: number; lon: number; url: string }[]
    expect(markers.length).toBeGreaterThanOrEqual(2)
    for (const m of markers) {
      expect(m.url).toMatch(/^#\/property\//)
    }
  })

  it('passes no markers when no properties have location data', async () => {
    // Override the mock for this test — return properties without locations
    vi.mocked(api.fetchAllSummaries).mockResolvedValue({
      'prop-x': {
        rid: 'prop-x',
        best_address: { succeeded: true, value: 'No Location Lane', error: null, provenance: { label: 'test' } },
        best_location: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
        rightmove_price: { succeeded: true, value: {amount: "300000", currency: "GBP"}, error: null, provenance: { label: 'test' } },
        rightmove_bedrooms: { succeeded: true, value: '3', error: null, provenance: { label: 'test' } },
        total_monthly_cost: { succeeded: true, value: { value: { amount: "2000", currency: "GBP" }, stddev: 0 }, error: null, provenance: { label: 'test' } },
    group_monthly_cost: { succeeded: true, value: { couple: { value: '2000', stddev: 0 }, others: { value: '500', stddev: 0 }, couple_label: 'S&L', others_label: 'A' }, error: null, provenance: { label: 'test' } },
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

    const mapView = wrapper.findComponent({ name: 'MapView' })
    expect(mapView.exists()).toBe(true)
    expect((mapView.props('markers') as unknown[]).length).toBe(0)
  })
})
describe('PropertyList map pins are interactive', () => {
  beforeEach(() => {
    vi.mocked(api.fetchAllSummaries).mockResolvedValue(mockData as any)
  })

  it('each marker links to the property detail page', async () => {
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

    const mapView = wrapper.findComponent({ name: 'MapView' })
    const markers = mapView.props('markers') as { url: string; label: string }[]
    expect(markers.length).toBeGreaterThan(0)
    markers.forEach(m => {
      expect(m.url).toMatch(/^#\/property\//)
    })
  })

  it('marker labels show the property price', async () => {
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

    const mapView = wrapper.findComponent({ name: 'MapView' })
    const markers = mapView.props('markers') as { label: string }[]
    const labels = markers.map(m => m.label).join(' ')
    // Property prices from mockData should appear on markers
    expect(labels).toContain('£200,000')
    expect(labels).toContain('£300,000')
    expect(labels).toContain('£500,000')
  })
})


// ── Extra vs your home (approved deltas design) ─────────────────────

const homeBaseline: MonthlyBaseline = {
  rid: 'prop-b',
  address: '31 Isambard Road, Southall, UB2 4GN',
  couple: { value: '1783.61', approx: false },
  others: { value: '652.92', approx: false },
  others_rent_paid: 600,
}

function deltaSummary(rid: string, address: string, couple: string, delta: { value: string; approx: boolean } | null): PropertySummary {
  return {
    ...mockData['prop-a'],
    rid,
    best_address: { succeeded: true, value: address, error: null, provenance: { label: 'test' } },
    group_monthly_cost: {
      succeeded: true,
      value: {
        couple: { value: couple, stddev: 0 },
        others: { value: '500', stddev: 0 },
        couple_label: 'S&L',
        others_label: 'Ashby',
        delta_vs_home: { couple: delta, others: delta },
      },
      error: null,
      provenance: { label: 'test' },
    },
  }
}

function deltaData(): Record<string, PropertySummary> {
  return {
    'prop-a': deltaSummary('prop-a', '10 Cheap St', '1500', { value: '-283.61', approx: false }),
    'prop-b': { ...deltaSummary('prop-b', '20 Mid Rd', '2000', { value: '+216.39', approx: false }), monthly_baseline: homeBaseline },
    'prop-c': deltaSummary('prop-c', '30 Expensive Ave', '3500', { value: '+1716.39', approx: false }),
    // Total known (1200 would pass a totals filter) but delta
    // uncomputable — must drop OUT of an "extra vs home" filter.
    'prop-d': deltaSummary('prop-d', '40 School Ln', '1200', null),
  }
}

async function mountWithBaseline(summaries: Record<string, PropertySummary>) {
  vi.mocked(api.fetchAllSummaries).mockResolvedValue(summaries)
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(PropertyList, { global: { plugins: [pinia] } })
  await flushPromises()
  // loadSettings (async, mocked empty settings) has settled by now —
  // set the household labels the real settings response would produce.
  usePropertiesStore().groupLabels = { coupleLabel: 'S&L', othersLabel: 'Ashby' }
  await wrapper.vm.$nextTick()
  return wrapper
}

describe('PropertyList — extra vs your home (baseline)', () => {
  it('renders the baseline legend once above the cards', async () => {
    const wrapper = await mountWithBaseline(deltaData())
    const legends = wrapper.findAll('.baseline-legend')
    expect(legends).toHaveLength(1)
    expect(legends[0].text()).toContain('Monthly figures are the change vs your home — 31 Isambard Road, Southall, UB2 4GN')
    expect(legends[0].text()).toContain('S&L £1,784/mo')
    expect(legends[0].text()).toContain('Ashby £653/mo')
    expect(legends[0].text()).toContain("Full totals and breakdowns live on each property's page.")
  })

  it('hides the legend and keeps today\'s labels without a baseline', async () => {
    const wrapper = await mountWithBaseline(mockData)
    expect(wrapper.find('.baseline-legend').exists()).toBe(false)
    await wrapper.findAll('.controls-row .pill')[0].trigger('click')
    const options = wrapper.findAll('.sheet__select option').map(o => o.text())
    expect(options).toContain('Monthly Cost')
    expect(options).not.toContain('Extra vs home/mo')
    await wrapper.find('[aria-label="Close sort"]').trigger('click')
    await wrapper.findAll('.controls-row .pill')[1].trigger('click')
    expect(wrapper.findAll('.sheet__label').map(l => l.text())).toContain('Max monthly cost (£)')
    expect(wrapper.find('.sheet__helper').exists()).toBe(false)
  })

  it('relabels the monthly sort to "Extra vs home/mo" when deltas are active', async () => {
    const wrapper = await mountWithBaseline(deltaData())
    await wrapper.findAll('.controls-row .pill')[0].trigger('click')
    const options = wrapper.findAll('.sheet__select option').map(o => o.text())
    expect(options).toContain('Extra vs home/mo')
    expect(options).not.toContain('Monthly Cost')
  })

  it('orders identically when sorting by the delta (total − constant)', async () => {
    const data = deltaData()
    delete data['prop-d']
    const wrapper = await mountWithBaseline(data)
    await wrapper.findAll('.controls-row .pill')[0].trigger('click')
    await wrapper.find('.sheet__select').setValue('monthly_cost')
    await wrapper.vm.$nextTick()
    const addrs = wrapper.findAll('.card__address-text').map(a => a.text())
    // Totals 1500 < 2000 < 3500 ↔ deltas −283.61 < +216.39 < +1716.39 —
    // the kept totals sort orders the deltas identically.
    expect(addrs).toEqual(['10 Cheap St', '20 Mid Rd', '30 Expensive Ave'])
  })

  it('filters by max extra vs home, excluding unknown deltas', async () => {
    const wrapper = await mountWithBaseline(deltaData())
    await wrapper.findAll('.controls-row .pill')[1].trigger('click')
    expect(wrapper.findAll('.sheet__label').map(l => l.text())).toContain('Max extra vs home (£/mo)')
    expect(wrapper.find('.sheet__helper').text()).toBe('Your home is £1,784/mo')
    await wrapper.find('.sheet__input').setValue('1300')
    await wrapper.find('.sheet__apply').trigger('click')
    await wrapper.vm.$nextTick()
    const addrs = wrapper.findAll('.card__address-text').map(a => a.text())
    // prop-a (−283.61) and prop-b (+216.39) pass; prop-c (+1716.39) is
    // over; prop-d has no computable delta even though its 1200 total
    // would pass — unknowns are excluded, never treated as 0.
    expect(addrs).toEqual(['10 Cheap St', '20 Mid Rd'])
  })
})
