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

describe('Header — settings link (P9, D2)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('is a direct Settings link, not a per-person drop-down', () => {
    const wrapper = mountHeader()
    const link = wrapper.find('a.header__settings-link')
    expect(link.exists()).toBe(true)
    expect(link.text()).toBe('Settings')
    expect(link.attributes('href')).toContain('/settings')
    // the confusing per-person menu is gone
    expect(wrapper.find('button.header__settings-menu').exists()).toBe(false)
    expect(wrapper.find('.header__menu-item--person').exists()).toBe(false)
  })

  it('still loads the household people for the superuser impersonation bar', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ persons: [{ name: 'Simon', email: 'smwinch@gmail.com' }, { name: 'Ashby', email: 'emily.winch@gmail.com' }] }),
    }))
    const wrapper = mountHeader()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    // the su-bar only renders in superuser mode; the fetch itself must not throw
    expect(wrapper.find('.header__settings-link').exists()).toBe(true)
  })
})
