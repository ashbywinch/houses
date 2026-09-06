import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { usePropertiesStore } from '../stores/properties'
import { useWebSocket } from '../composables/useWebSocket'
import { fetchAllSummaries, fetchSettings, fetchWhatIfState } from '../services/api'
import { flushPromises } from '@vue/test-utils'

vi.mock('../services/api', () => ({
  fetchAllSummaries: vi.fn().mockResolvedValue({}),
  fetchPropertyDetail: vi.fn().mockResolvedValue(null),
  fetchSettings: vi.fn().mockResolvedValue({}),
  fetchWhatIfState: vi.fn<() => Promise<boolean>>(),
  patchTriage: vi.fn(),
}))

/**
 * Tests for the WebSocket message handling code path in App.vue.
 * App.vue calls useWebSocket().connect() in its setup, which this
 * test exercises without mounting the full component tree.
 */
describe('WebSocket message handler', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('handles property_updated message without throwing', () => {
    const store = usePropertiesStore()
    const { connect, disconnect } = useWebSocket((_url: string) => {
      const ws = {
        onopen: null as any,
        onclose: null as any,
        onmessage: null as any,
        close() { this.onclose?.() },
      }
      // Capture onmessage for direct invocation
      setTimeout(() => {
        ws.onmessage?.({
          data: JSON.stringify({
            type: 'property_updated',
            rid: 'test-rid',
            data: {
              rid: 'test-rid',
              best_address: { succeeded: true, value: '1 Test Rd', error: null, provenance: { label: 'ws' } },
              schools: {
                primary: { school: { succeeded: true, value: { name: 'WS Primary', ofsted: 'Good', distance: {value: 1, unit: 'km'}, url: '' }, error: null, provenance: { label: 'ws' } } },
                secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'ws' } } },
              },
            },
          }),
        })
      }, 0)
      return ws as any
    })

    connect('ws://localhost/api/ws')

    // Wait for the microtask that processes the WS message
    return new Promise<void>((resolve) => {
      setTimeout(() => {
        expect(store.summaries['test-rid']).toBeDefined()
        expect(store.rids).toContain('test-rid')
        disconnect()
        resolve()
      }, 10)
    })
  })

  it('processes empty ofsted from WebSocket without crashing', () => {
    const store = usePropertiesStore()
    const { connect, disconnect } = useWebSocket((_url: string) => {
      const ws = {
        onopen: null as any,
        onclose: null as any,
        onmessage: null as any,
        close() { this.onclose?.() },
      }
      setTimeout(() => {
        ws.onmessage?.({
          data: JSON.stringify({
            type: 'property_updated',
            rid: 'empty-ofsted',
            data: {
              rid: 'empty-ofsted',
              best_address: { succeeded: true, value: '2 School Ln', error: null, provenance: { label: 'ws' } },
              schools: {
                primary: { school: { succeeded: true, value: { name: 'Empty Ofsted Primary', ofsted: '', distance: {value: 1, unit: 'km'}, url: '' }, error: null, provenance: { label: 'ws' } } },
              },
            },
          }),
        })
      }, 0)
      return ws as any
    })

    connect('ws://localhost/api/ws')

    return new Promise<void>((resolve) => {
      setTimeout(() => {
        const summary = store.summaries['empty-ofsted']
        expect(summary?.schools?.primary?.school?.value?.ofsted).toBe('')
        disconnect()
        resolve()
      }, 10)
    })
  })
})

/**
 * Tests for the loadAll() data loading path.
 * PropertyList.vue calls store.loadAll() in onMounted, which
 * fetches data from the API and populates the store.
 * Tests that bypass App.vue set store data directly instead.
 */
describe('loadAll data flow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(fetchSettings).mockResolvedValue({})
  })

  it('populates store from API response with empty ofsted without error', async () => {
    const store = usePropertiesStore()

    const apiData: Record<string, any> = {
      'prop-1': {
        rid: 'prop-1',
        best_address: { succeeded: true, value: '1 Main St', error: null, provenance: { label: 'api' } },
        best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'api' } },
        rightmove_price: { succeeded: true, value: { amount: "300000", currency: 'GBP' }, error: null, provenance: { label: 'api' } },
        rightmove_bedrooms: { succeeded: true, value: '3', error: null, provenance: { label: 'api' } },
        total_monthly_cost: { succeeded: true, value: { value: { amount: "1800", currency: "GBP" }, stddev: 0 }, error: null, provenance: { label: 'api' } },
        town_name: { succeeded: true, value: 'Testown', error: null, provenance: { label: 'api' } },
        commutes: {},
        schools: {
          primary: { school: { succeeded: true, value: { name: 'API Primary', ofsted: 'Good', distance: {value: 1, unit: 'km'}, url: '' }, error: null, provenance: { label: 'api' } } },
          secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'api' } } },
        },
        walkability: { succeeded: true, value: { walk_to_town: {value: 10, unit: 'minute'} }, error: null, provenance: { label: 'api' } },
        freshness: { property_added_at: '2026-07-15T10:00:00+00:00' },
      },
      'prop-2': {
        rid: 'prop-2',
        best_address: { succeeded: true, value: '2 High Rd', error: null, provenance: { label: 'api' } },
        best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'api' } },
        rightmove_price: { succeeded: true, value: { amount: "500000", currency: 'GBP' }, error: null, provenance: { label: 'api' } },
        rightmove_bedrooms: { succeeded: true, value: '4', error: null, provenance: { label: 'api' } },
        total_monthly_cost: { succeeded: true, value: { value: { amount: "2500", currency: "GBP" }, stddev: 0 }, error: null, provenance: { label: 'api' } },
        town_name: { succeeded: true, value: 'Big Town', error: null, provenance: { label: 'api' } },
        commutes: { 'Simon/Office': { commute: { succeeded: true, value: { duration: { value: 45, unit: 'minute' } }, error: null, provenance: { label: 'api' } } } },
        schools: {
          primary: { school: { succeeded: true, value: { name: 'Empty Ofsted School', ofsted: '', distance: {value: 2, unit: 'km'}, url: '' }, error: null, provenance: { label: 'api' } } },
          secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'api' } } },
        },
        walkability: { succeeded: false, value: null, error: null, provenance: { label: 'api' } },
        freshness: { property_added_at: '2026-07-14T10:00:00+00:00' },
      },
    }

    vi.mocked(fetchAllSummaries).mockResolvedValue(apiData as any)
    await store.loadAll()

    expect(store.rids).toContain('prop-1')
    expect(store.rids).toContain('prop-2')
    expect(store.summaries['prop-2']?.schools?.primary?.school?.value?.ofsted).toBe('')
  })
})

describe('WebSocket settings broadcast', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('refreshes cached settings when the DAG broadcasts a settings node update', () => {
    const store = usePropertiesStore()
    store.commuteGoods = { Simon: 30 }
    store.commuteCeilings = { Simon: { fine: 45, isChild: false } }

    // The settings node changes server-side (e.g. a PATCH from another
    // device); the next fetchSettings returns the new bands.
    vi.mocked(fetchSettings).mockResolvedValue({
      persons: { value: [] },
      commute_thresholds: {
        value: { Simon: { good_max_minutes: 35, fine_max_minutes: 50 } },
      },
    } as unknown as Record<string, unknown>)

    const { connect, disconnect } = useWebSocket((_url: string) => {
      const ws = {
        onopen: null as any,
        onclose: null as any,
        onmessage: null as any,
        close() { this.onclose?.() },
      }
      setTimeout(() => {
        ws.onmessage?.({
          data: JSON.stringify({ type: 'node_updated', node_id: 'commute_thresholds', data: {} }),
        })
      }, 0)
      return ws as any
    })

    connect('ws://localhost/api/ws')

    return new Promise<void>((resolve) => {
      setTimeout(() => {
        expect(store.commuteGoods['Simon']).toBe(35)
        expect(store.commuteCeilings['Simon'].fine).toBe(50)
        disconnect()
        resolve()
      }, 10)
    })
  })

  it('ignores node_updated messages for property nodes', () => {
    const fetchSpy = vi.mocked(fetchSettings)

    const { connect, disconnect } = useWebSocket((_url: string) => {
      const ws = {
        onopen: null as any,
        onclose: null as any,
        onmessage: null as any,
        close() { this.onclose?.() },
      }
      setTimeout(() => {
        ws.onmessage?.({
          data: JSON.stringify({ type: 'node_updated', node_id: 'some-rid/commute/total_monthly', data: {} }),
        })
      }, 0)
      return ws as any
    })

    connect('ws://localhost/api/ws')
    // the store loaded settings at init — count from here
    const callsBefore = fetchSpy.mock.calls.length

    return new Promise<void>((resolve) => {
      setTimeout(() => {
        expect(fetchSpy.mock.calls.length).toBe(callsBefore)
        disconnect()
        resolve()
      }, 10)
    })
  })

  it('refreshes the what-if mode when the DAG broadcasts a settings node update', async () => {
    const store = usePropertiesStore()
    // Capture the socket's message handler so the broadcast is invoked
    // directly — no wall-clock timers.
    let deliver: ((msg: { data: string }) => void) | undefined
    const { connect, disconnect } = useWebSocket((_url: string) => {
      const ws = {
        onopen: null,
        onclose: null,
        onmessage: null as ((msg: { data: string }) => void) | null,
        close() { /* test calls disconnect() directly */ },
      }
      deliver = (msg: { data: string }) => ws.onmessage?.(msg)
      // the handler only reads onmessage; a full socket is unnecessary
      return ws as unknown as WebSocket
    })
    connect('ws://localhost/api/ws')

    // a what-if applied on another device writes through the settings
    // nodes — the broadcast must flip this device's mode flag
    vi.mocked(fetchWhatIfState).mockResolvedValue(true)
    deliver!({ data: JSON.stringify({ type: 'node_updated', node_id: 'settings/financial', data: {} }) })
    await flushPromises()

    expect(store.whatIfActive).toBe(true)
    disconnect()
  })
})
