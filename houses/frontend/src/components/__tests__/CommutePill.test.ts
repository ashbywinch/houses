import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import CommutePill from '../CommutePill.vue'

describe('CommutePill optional cost', () => {
  it('renders without cost prop', () => {
    const wrapper = mount(CommutePill, {
      props: { label: 'Office', duration: 32 },
    })
    expect(wrapper.text()).toContain('32m')
    expect(wrapper.text()).not.toContain('£')
  })

  it('renders with cost prop', () => {
    const wrapper = mount(CommutePill, {
      props: { label: 'Office', duration: 32, cost: 4.5 },
    })
    expect(wrapper.text()).toContain('£4.50')
  })
})
