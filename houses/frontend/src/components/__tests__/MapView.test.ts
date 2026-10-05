import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import MapView from '../MapView.vue'

// Leaflet needs a real layout engine — jsdom can't size a map. The stub
// keeps the component mounting; the polygon/layerGroup calls are the
// observable "the map drew this".
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

import L from 'leaflet'

const coords = [[51.5, -0.1], [51.6, -0.2]]

describe('MapView — the map owns its isochrone fetch', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('fetches /api/map/isochrones itself, keys the layers and draws them', async () => {
    const fetchMock = vi.fn(async (url: unknown) => {
      if (String(url) === '/api/map/isochrones') {
        return {
          ok: true,
          json: async () => ({
            layers: [{ name: 'Train shed', color: '#e11d48', visibleByDefault: true, polygons: [{ coords }] }],
          }),
        } as unknown as Response
      }
      throw new Error(`unexpected fetch: ${String(url)}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(MapView, { props: { markers: [], isochrones: true } })
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(fetchMock).toHaveBeenCalledWith('/api/map/isochrones')
    expect(wrapper.find('.mapview-key__name').text()).toBe('Train shed')
    const polygon = L.polygon as unknown as { mock: { calls: unknown[][] } }
    expect(polygon.mock.calls.some(c => JSON.stringify(c[0]) === JSON.stringify(coords))).toBe(true)
  })

  it('requests nothing for an embed without isochrones (the detail-page map)', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(MapView, { props: { markers: [] } })
    await flushPromises()

    expect(fetchMock).not.toHaveBeenCalled()
    expect(wrapper.find('.mapview-key').exists()).toBe(false)
  })
})
