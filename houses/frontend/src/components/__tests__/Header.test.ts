import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import Header from '../Header.vue'
import { useAuthStore } from '../../stores/auth'

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }, { path: '/settings', component: { template: '<div />' } }],
  })
}

function mountHeader() {
  const pinia = createPinia()
  const auth = useAuthStore(pinia)
  auth.user = { email: 'emily.winch@gmail.com', name: 'Emily', picture: '', person: 'Ashby', is_superuser: false } as any
  auth.loading = false
  return mount(Header, { props: { title: 'Test' }, global: { plugins: [pinia, makeRouter()] } })
}

describe('Header — person/settings drop-down (P9, D2)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the settings menu with the household people', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ persons: [{ name: 'Simon', email: 'smwinch@gmail.com' }, { name: 'Ashby', email: 'emily.winch@gmail.com' }] }),
    }))
    const wrapper = mountHeader()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    await wrapper.find('button.header__settings-menu').trigger('click')
    await wrapper.vm.$nextTick()
    const text = wrapper.text()
    expect(text).toContain('Simon')
    expect(text).toContain('Ashby')
  })

  it('links each person to their settings section', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ persons: [{ name: 'Simon', email: 'smwinch@gmail.com' }] }),
    }))
    const wrapper = mountHeader()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    await wrapper.find('button.header__settings-menu').trigger('click')
    await wrapper.vm.$nextTick()
    const link = wrapper.findAll('a.header__menu-item--person').find(a => a.text() === 'Simon')
    expect(link?.exists()).toBe(true)
    expect(link?.attributes('href') ?? '').toContain('settings?person=Simon')
  })
})
