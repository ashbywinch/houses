import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import App from '../App.vue'
import router from '../router'
import type { PropertySummary } from '../types'

vi.mock('../services/api', () => ({
  fetchAllSummaries: vi.fn().mockResolvedValue({}),
  fetchPropertyDetail: vi.fn().mockResolvedValue(null),
  fetchSettings: vi.fn().mockResolvedValue({}),
  fetchWhatIfState: vi.fn().mockResolvedValue(false),
  patchTriage: vi.fn(),
}))

// Leaflet needs a real layout engine; PropertyList imports MapView, so
// stub it the way the view tests do.
vi.mock('leaflet', () => ({
  default: {
    map: vi.fn(() => ({
      addLayer: vi.fn(),
      remove: vi.fn(),
      fitBounds: vi.fn(),
      invalidateSize: vi.fn(),
      on: vi.fn(),
      removeLayer: vi.fn(),
      hasLayer: vi.fn(() => false),
    })),
    tileLayer: vi.fn(() => ({ addTo: vi.fn() })),
    layerGroup: vi.fn(() => ({ addTo: vi.fn(), clearLayers: vi.fn() })),
    polygon: vi.fn(() => ({ bindPopup: vi.fn(), addTo: vi.fn() })),
    marker: vi.fn(() => ({ bindPopup: vi.fn(), addTo: vi.fn() })),
    divIcon: vi.fn(() => ({})),
    latLngBounds: vi.fn(() => ({ pad: vi.fn(() => ({})) })),
  },
}))
vi.mock('leaflet/dist/leaflet.css', () => ({}))

import * as api from '../services/api'

/** The mounted App under test — unmounted between tests so a stale
 *  RouterView (e.g. LoginPage) cannot react to the shared router. */
let mounted: { unmount: () => void } | null = null

class MockWebSocket {
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  close() { /* no-op */ }
}

const summary: PropertySummary = {
  rid: 'p1',
  best_address: { succeeded: true, value: '1 Auth Gated Street', error: null, provenance: { label: 'test' } },
  best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
  rightmove_price: { succeeded: true, value: { amount: '200000', currency: 'GBP' }, error: null, provenance: { label: 'test' } },
  rightmove_bedrooms: { succeeded: true, value: '3', error: null, provenance: { label: 'test' } },
  walkability: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
  commutes: {},
  schools: {
    primary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
    secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
  },
} as unknown as PropertySummary

/** /api/auth/me that never answers — the check stays in flight while
 *  the test asserts what the index painted in the meantime. */
function authPending() {
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})))
}

/** /api/auth/me answered immediately with the given session state. */
function authAnswers(authenticated: boolean) {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => authenticated
      ? { authenticated: true, email: 'a@b.c', name: 'Ashby', picture: '', person: null, person_id: null, is_superuser: false }
      : { authenticated: false },
  } as unknown as Response)))
}

beforeEach(() => {
  vi.mocked(api.fetchAllSummaries).mockResolvedValue({ p1: summary })
  vi.stubGlobal('WebSocket', MockWebSocket)
})

afterEach(() => {
  mounted?.unmount()
  mounted = null
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

/** Mount the real App + real router at the index. The shared router is
 *  parked on /login first so each test starts from a known route. */
async function mountIndex() {
  const pinia = createPinia()
  setActivePinia(pinia)
  await router.replace('/login')
  const wrapper = mount(App, { global: { plugins: [pinia, router] } })
  mounted = wrapper
  await router.push('/')
  await flushPromises()
  return wrapper
}

describe('router — /api/auth/me does not gate the first paint', () => {
  it('paints the index shell while the auth check is still pending, and holds the data back', async () => {
    authPending()
    const wrapper = await mountIndex()

    // The shell is on screen while the ~800 ms auth round trip is still
    // pending...
    expect(wrapper.find('.tab-bar').exists()).toBe(true)
    expect(wrapper.find('.search-section').exists()).toBe(true)
    expect(router.currentRoute.value.path).toBe('/')
    // ...and no property data has been painted.
    expect(wrapper.text()).not.toContain('1 Auth Gated Street')
  })

  it('sends an unauthenticated visitor to /login once the check answers', async () => {
    authAnswers(false)
    const wrapper = await mountIndex()

    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/login'))
    expect(wrapper.text()).toContain('Sign in to continue')
    expect(wrapper.text()).not.toContain('1 Auth Gated Street')
  })

  it('opens the gate once an authenticated check answers', async () => {
    authAnswers(true)
    const wrapper = await mountIndex()

    await vi.waitFor(() => expect(wrapper.text()).toContain('1 Auth Gated Street'))
    expect(router.currentRoute.value.path).toBe('/')
  })
})
