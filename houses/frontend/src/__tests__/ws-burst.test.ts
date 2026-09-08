import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { usePropertiesStore } from '../stores/properties'
import { useWebSocket } from '../composables/useWebSocket'
import { fetchSettings } from '../services/api'


vi.mock('../services/api', () => ({
  fetchAllSummaries: vi.fn().mockResolvedValue({}),
  fetchPropertyDetail: vi.fn().mockResolvedValue(null),
  fetchSettings: vi.fn().mockResolvedValue({}),
  fetchWhatIfState: vi.fn().mockResolvedValue(false),
  patchTriage: vi.fn(),
}))

function summaryFor(rid: string, marker: string) {
  return {
    rid,
    best_address: { succeeded: true, value: `${rid} ${marker}`, error: null, provenance: { label: 'ws' } },
    rightmove_price: { succeeded: true, value: { amount: '200000', currency: 'GBP' }, error: null, provenance: { label: 'ws' } },
    commutes: {},
    schools: {},
  }
}

/**
 * A scenario apply produces a BURST of websocket updates (one per
 * refreshed node/property). The store must apply the burst as a small
 * number of batched mutations — one reactive invalidation per burst,
 * not one per message — or the phone re-renders most of the page over
 * and over while the backlog drains.
 */
describe('scenario update burst', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('applies a 45-message backlog drain as one batched store update', async () => {
    const store = usePropertiesStore()
    const rids = Array.from({ length: 45 }, (_, i) => `prop-${i}`)
    const { connect, disconnect } = useWebSocket((_url: string) => {
      const ws = {
        onopen: null as any,
        onclose: null as any,
        onmessage: null as any,
        close() { this.onclose?.() },
      }
      setTimeout(() => {
        for (const rid of rids) {
          ws.onmessage?.({
            data: JSON.stringify({
              type: 'property_updated',
              rid,
              data: summaryFor(rid, 'after-scenario'),
            }),
          })
        }
      }, 0)
      return ws as any
    })

    let mutations = 0
    store.$subscribe(() => { mutations += 1 })

    connect('ws://localhost/api/ws')
    await new Promise<void>((resolve) => setTimeout(resolve, 50))

    for (const rid of rids) {
      expect(store.summaries[rid]).toBeDefined()
    }
    // THE CONTRACT: the whole backlog drain is applied as one batch —
    // a handful of reactive invalidations, not one per message. This is
    // the whole-page redraw: 45 sequential invalidations re-render the
    // grid over and over while the backlog drains.
    expect(mutations).toBeLessThanOrEqual(3)
    disconnect()
  })

  it('coalesces a settings-node burst into one settings refresh', async () => {
    // Create the store first: its constructor fetches settings once at
    // init — that call is not part of the burst.
    usePropertiesStore()
    const settingsMock = vi.mocked(fetchSettings)
    settingsMock.mockClear()
    settingsMock.mockResolvedValue({})
    const { connect, disconnect } = useWebSocket((_url: string) => {
      const ws = {
        onopen: null as any,
        onclose: null as any,
        onmessage: null as any,
        close() { this.onclose?.() },
      }
      setTimeout(() => {
        for (let i = 0; i < 10; i++) {
          ws.onmessage?.({
            data: JSON.stringify({ type: 'node_updated', node_id: 'persons' }),
          })
        }
      }, 0)
      return ws as any
    })

    connect('ws://localhost/api/ws')
    await new Promise<void>((resolve) => setTimeout(resolve, 50))

    // THE CONTRACT: ten settings broadcasts in one tick refetch the
    // cached settings once, not ten times.
    expect(settingsMock).toHaveBeenCalledTimes(1)
    disconnect()
  })
  it('applies a 3-message burst as one batched store update', async () => {
    const store = usePropertiesStore()
    const { connect, disconnect } = useWebSocket((_url: string) => {
      const ws = {
        onopen: null as any,
        onclose: null as any,
        onmessage: null as any,
        close() { this.onclose?.() },
      }
      setTimeout(() => {
        for (const rid of ['prop-a', 'prop-b', 'prop-c']) {
          ws.onmessage?.({
            data: JSON.stringify({
              type: 'property_updated',
              rid,
              data: summaryFor(rid, 'after-scenario'),
            }),
          })
        }
      }, 0)
      return ws as any
    })

    let mutations = 0
    store.$subscribe(() => { mutations += 1 })

    connect('ws://localhost/api/ws')
    await new Promise<void>((resolve) => setTimeout(resolve, 50))

    expect(store.summaries['prop-a']).toBeDefined()
    // THE CONTRACT: the burst is applied in one batch — a handful of
    // mutations at most, not one per message.
    expect(mutations).toBeLessThanOrEqual(3)
    disconnect()
  })
})
