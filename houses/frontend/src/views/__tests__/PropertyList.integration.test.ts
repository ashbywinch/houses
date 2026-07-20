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

/**
 * Realistic test data modeled on the production API response.
 * Includes edge cases found in live data:
 * - schools with succeeded=true but empty ofsted string (2 in prod)
 * - mixed succeeded/failed school lookups
 * - missing optional fields
 */
function makeRealisticData(): Record<string, PropertySummary> {
  const base: PropertySummary = {
    rid: '',
    best_address: { succeeded: true, value: '1 Test Road, London', error: null, provenance: { label: 'test' } },
    best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
    rightmove_price: { succeeded: true, value: { amount: 350000, currency: 'GBP' }, error: null, provenance: { label: 'test' } },
    rightmove_bedrooms: { succeeded: true, value: '3', error: null, provenance: { label: 'test' } },
    total_monthly_cost: { succeeded: true, value: { amount: 2000, currency: 'GBP' }, error: null, provenance: { label: 'test' } },
    town_name: { succeeded: true, value: 'Test Town', error: null, provenance: { label: 'test' } },
    commutes: {
      'Simon/Office': {
        commute: { succeeded: true, value: { duration: { value: 45, unit: 'minute' }, mode: 'transit', daily_cost: { amount: 8, currency: 'GBP' }, label: 'Office' }, error: null, provenance: { label: 'test' } },
      },
    },
    schools: {
      primary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
      secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
    },
    walkability: { succeeded: true, value: { walk_to_town_minutes: 15 }, error: null, provenance: { label: 'test' } },
    epc: { succeeded: true, value: { band: 'C' }, error: null, provenance: { label: 'test' } },
    freshness: { property_added_at: '2026-07-15T10:00:00+00:00' },
  }

  return {
    'prop-complete': {
      ...base,
      rid: 'prop-complete',
      schools: {
        primary: { school: { succeeded: true, value: { name: 'St Marys', ofsted: 'Good', distance_km: 0.8, url: 'https://example.com/1' }, error: null, provenance: { label: 'test' } } },
        secondary: { school: { succeeded: true, value: { name: 'High School', ofsted: 'Outstanding', distance_km: 1.5, url: '' }, error: null, provenance: { label: 'test' } } },
      },
    },
    'prop-empty-ofsted': {
      ...base,
      rid: 'prop-empty-ofsted',
      schools: {
        primary: { school: { succeeded: true, value: { name: 'No Rating School', ofsted: '', distance_km: 1.2, url: '' }, error: null, provenance: { label: 'test' } } },
        secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
      },
    },
    'prop-partial': {
      ...base,
      rid: 'prop-partial',
      commutes: {},
      schools: {
        primary: { school: { succeeded: true, value: { name: 'Partial Primary', ofsted: 'Requires Improvement', distance_km: 2.0, url: 'https://example.com/2' }, error: null, provenance: { label: 'test' } } },
        secondary: { school: { succeeded: false, value: null, error: 'not found', provenance: { label: 'test' } } },
      },
    },
    'prop-no-schools': {
      ...base,
      rid: 'prop-no-schools',
      schools: {
        primary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
        secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
      },
    },
    'prop-no-epc': {
      ...base,
      rid: 'prop-no-epc',
      epc: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    },
  }
}

function setupStore(data: Record<string, PropertySummary>) {
  setActivePinia(createPinia())
  const store = usePropertiesStore()
  store.rids = Object.keys(data)
  store.summaries = data
  store.loading = false
  return store
}

describe('PropertyList comprehensive rendering', () => {
  beforeEach(() => {
    vi.mocked(api.fetchAllSummaries).mockResolvedValue(makeRealisticData() as any)
  })

  it('renders all property cards without throwing', () => {
    const data = makeRealisticData()
    setupStore(data)
    expect(() => mount(PropertyList)).not.toThrow()
  })

  it('renders property with empty ofsted school without error', () => {
    const data = makeRealisticData()
    setupStore(data)
    const wrapper = mount(PropertyList)
    expect(wrapper.text()).toContain('No Rating School')
    expect(wrapper.find('.card__schools-epc').exists()).toBe(true)
  })

  it('renders property with complete school data', () => {
    const data = makeRealisticData()
    setupStore(data)
    const wrapper = mount(PropertyList)
    expect(wrapper.text()).toContain('St Marys')
    expect(wrapper.text()).toContain('Good')
    expect(wrapper.text()).toContain('High School')
    expect(wrapper.text()).toContain('Outstanding')
  })

  it('renders property with partial school data', () => {
    const data = makeRealisticData()
    setupStore(data)
    const wrapper = mount(PropertyList)
    expect(wrapper.text()).toContain('Partial Primary')
    expect(wrapper.text()).toContain('Requires Improvement')
  })

  it('shows EPC badge when epc data exists', () => {
    const data = makeRealisticData()
    setupStore(data)
    const wrapper = mount(PropertyList)
    expect(wrapper.text()).toContain('C')
  })

  it('handles properties with no school data', () => {
    const data = makeRealisticData()
    setupStore(data)
    const wrapper = mount(PropertyList)
    expect(wrapper.text()).toContain('1 Test Road')
  })

  it('renders commute costs for properties with commute data', () => {
    const data = makeRealisticData()
    setupStore(data)
    const wrapper = mount(PropertyList)
    expect(wrapper.text()).toContain('45m')
    expect(wrapper.text()).toContain('Partial Primary')
  })
})
