import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { usePropertiesStore } from '../properties'
import type { PropertyDetail } from '../../types'

vi.mock('../../services/api', () => ({
  fetchPropertyDetail: vi.fn(),
  fetchAllSummaries: vi.fn(),
  fetchSettings: vi.fn(),
}))

import * as api from '../../services/api'

function mockDetail(rid: string): PropertyDetail {
  return {
    rid,
    best_address: { succeeded: true, value: '1 Main St', error: null, provenance: { label: 'test' } },
    rightmove_url: { succeeded: true, value: '', error: null, provenance: { label: 'test' } },
    rightmove_price: { succeeded: true, value: {amount: "500000", currency: "GBP"}, error: null, provenance: { label: 'test' } },
    rightmove_bedrooms: { succeeded: true, value: '3', error: null, provenance: { label: 'test' } },
    postcode: { succeeded: true, value: 'SW1V 2QQ', error: null, provenance: { label: 'test' } },
    location: {
      best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
      geocode: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
      rightmove_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
      precise_location: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    },
    commutes: {},
    schools: {
      primary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
      secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
    },
    affordability: {
      stamp_duty: { succeeded: true, value: {amount: "0", currency: "GBP"}, error: null, provenance: { label: 'test' } },
      council_tax: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      monthly_mortgage: { succeeded: true, value: {amount: "0", currency: "GBP"}, error: null, provenance: { label: 'test' } },
      monthly_sinking_fund: { succeeded: true, value: {amount: "0", currency: "GBP"}, error: null, provenance: { label: 'test' } },
      monthly_commute_cost: { succeeded: true, value: { persons: {}, yearly_total_gbp: 0, formula_explanation: '' }, error: null, provenance: { label: 'test' } },
      total_monthly_housing_cost: { succeeded: true, value: {amount: "0", currency: "GBP"}, error: null, provenance: { label: 'test' } },
    },
    area: {
      walkability: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      town_description: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    },
    comments: {
      status: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      status_reason: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      group_notes: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      ashby_comments: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      ashby_works_estimate: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      design_needed: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      planning_needed: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    },
    settings: {
      persons: { succeeded: true, value: [], error: null, provenance: { label: 'test' } },
      financial: { succeeded: true, value: {}, error: null, provenance: { label: 'test' } },
    },
  }
}

describe('properties store loadDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
  })

  it('loads detail on first call', async () => {
    const store = usePropertiesStore()
    const fetchMock = vi.mocked(api.fetchPropertyDetail)
    fetchMock.mockResolvedValueOnce(mockDetail('prop123'))

    const result = await store.loadDetail('prop123')
    expect(result).toBeDefined()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('returns cached without re-fetching on second call', async () => {
    const store = usePropertiesStore()
    const fetchMock = vi.mocked(api.fetchPropertyDetail)
    fetchMock.mockResolvedValueOnce(mockDetail('prop456'))

    await store.loadDetail('prop456')
    const result = await store.loadDetail('prop456')
    expect(result).toBeDefined()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('re-fetches when force=true even with cached data', async () => {
    const store = usePropertiesStore()
    const fetchMock = vi.mocked(api.fetchPropertyDetail)
    fetchMock.mockResolvedValue(mockDetail('prop789'))

    await store.loadDetail('prop789')
    await store.loadDetail('prop789', true)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('retries on error when called again', async () => {
    const store = usePropertiesStore()
    const fetchMock = vi.mocked(api.fetchPropertyDetail)
    fetchMock.mockRejectedValueOnce(new Error('Network error'))
    fetchMock.mockResolvedValueOnce(mockDetail('prop999'))

    const first = await store.loadDetail('prop999')
    expect(first).toBeNull()
    expect(store.error).toBe('Something went wrong loading this property. Please try again.')

    const second = await store.loadDetail('prop999')
    expect(second).toBeDefined()
    expect(store.error).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})

describe('properties store triage state', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(api.fetchAllSummaries).mockResolvedValue({
      'prop1': {
        rid: 'prop1',
        best_address: { succeeded: true, value: '1 Main St', error: null, provenance: { label: 'test' } },
        best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
        rightmove_price: { succeeded: true, value: {amount: "500000", currency: "GBP"}, error: null, provenance: { label: 'test' } },
        rightmove_bedrooms: { succeeded: true, value: '3', error: null, provenance: { label: 'test' } },
        total_monthly_cost: { succeeded: true, value: {amount: "2500", currency: "GBP"}, error: null, provenance: { label: 'test' } },
        walkability: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
        town_name: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
        commutes: {},
        schools: {
          primary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
          secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
        },
        // Simulate API returning AttemptValue-wrapped triage (as the backend actually does)
        triage: {
          favourite: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
          dismissed: { succeeded: true, value: true, error: null, provenance: { label: 'test' } },
          is_viewed: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
          user_notes: { succeeded: true, value: 'some notes', error: null, provenance: { label: 'test' } },
          triage_status: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
        },
      },
    })
  })

  it('extracts raw boolean/string values from AttemptValue wrappers', async () => {
    const store = usePropertiesStore()
    await store.loadAll()

    const t = store.triage['prop1']
    // These should be booleans, not AttemptValue objects
    expect(t.favourite).toBe(false)       // value was null → false
    expect(t.dismissed).toBe(true)        // value was true → true
    expect(t.is_viewed).toBe(false)       // value was null → false
    expect(t.user_notes).toBe('some notes') // string
    expect(t.triage_status).toBe('')      // value was null → ''
  })

  it('triages are booleans not objects so toggle logic works', async () => {
    const store = usePropertiesStore()
    await store.loadAll()

    const t = store.triage['prop1']
    // The card checks `triage?.favourite` for truthiness — an AttemptValue object
    // is always truthy, a boolean false is falsy. This test verifies booleans.
    expect(typeof t.favourite).toBe('boolean')
    expect(typeof t.dismissed).toBe('boolean')
    expect(typeof t.is_viewed).toBe('boolean')
  })
})
