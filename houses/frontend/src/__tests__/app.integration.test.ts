import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createWebHashHistory } from 'vue-router'
import { mount } from '@vue/test-utils'
import App from '../App.vue'
import PropertyList from '../views/PropertyList.vue'
import PropertyDetail from '../views/PropertyDetail.vue'

vi.mock('../services/api', () => ({
  fetchAllSummaries: vi.fn().mockResolvedValue({}),
  fetchPropertyDetail: vi.fn().mockResolvedValue(null),
  fetchSettings: vi.fn().mockResolvedValue({}),
  patchTriage: vi.fn(),
}))

import * as api from '../services/api'

const mockWebSocket = vi.fn()
class MockWebSocket {
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  close() { this.onclose?.() }
  constructor(url: string) { mockWebSocket(url) }
}

describe('Full app bootstrap', () => {
  beforeEach(() => {
    vi.mocked(api.fetchAllSummaries).mockResolvedValue({})
    vi.stubGlobal('WebSocket', MockWebSocket as any)
  })

  it('mounts without throwing', () => {
    const pinia = createPinia()
    const router = createRouter({
      history: createWebHashHistory(),
      routes: [
        { path: '/', component: PropertyList },
        { path: '/property/:rid', component: PropertyDetail },
      ],
    })
    expect(() => mount(App, { global: { plugins: [pinia, router] } })).not.toThrow()
  })

  it('renders RouterView with default route', async () => {
    const pinia = createPinia()
    const router = createRouter({
      history: createWebHashHistory(),
      routes: [
        { path: '/', component: PropertyList },
        { path: '/property/:rid', component: PropertyDetail },
      ],
    })
    const wrapper = mount(App, { global: { plugins: [pinia, router] } })
    router.push('/')
    await router.isReady()
    await wrapper.vm.$nextTick()
    expect(wrapper.findComponent(PropertyList).exists()).toBe(true)
  })

  it('navigates to property detail route', async () => {
    const pinia = createPinia()
    const router = createRouter({
      history: createWebHashHistory(),
      routes: [
        { path: '/', component: PropertyList },
        { path: '/property/:rid', component: PropertyDetail },
      ],
    })
    const wrapper = mount(App, { global: { plugins: [pinia, router] } })
    router.push('/property/123')
    await router.isReady()
    await wrapper.vm.$nextTick()
    expect(wrapper.findComponent(PropertyDetail).exists()).toBe(true)
  })
})

describe('App.vue isolated', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', MockWebSocket as any)
    setActivePinia(createPinia())
  })

  it('mounts without error', () => {
    const router = createRouter({
      history: createWebHashHistory(),
      routes: [
        { path: '/', component: PropertyList },
        { path: '/property/:rid', component: PropertyDetail },
      ],
    })
    expect(() => mount(App, { global: { plugins: [createPinia(), router] } })).not.toThrow()
  })
})
