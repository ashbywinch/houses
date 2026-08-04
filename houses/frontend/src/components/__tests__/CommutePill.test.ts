import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import CommutePill from '../CommutePill.vue'

describe('CommutePill optional cost', () => {
  it('renders without cost prop', () => {
    const wrapper = mount(CommutePill, {
      props: { label: 'Office', duration: 32 },
      global: { plugins: [createPinia()] },
    })
    expect(wrapper.text()).toContain('32m')
    expect(wrapper.text()).not.toContain('£')
  })

  it('renders with cost prop', () => {
    const wrapper = mount(CommutePill, {
      props: { label: 'Office', duration: 32, cost: 4.5 },
      global: { plugins: [createPinia()] },
    })
    expect(wrapper.text()).toContain('£4.50')
  })
})

describe('CommutePill missing-route state (A4)', () => {
  it('explains a ? duration as a missing route', () => {
    const wrapper = mount(CommutePill, { props: { label: '', duration: null } })
    expect(wrapper.text()).toContain('?')
    expect(wrapper.attributes('title')).toContain('No route found')
  })
})
