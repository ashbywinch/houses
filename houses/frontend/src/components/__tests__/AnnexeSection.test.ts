import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import AnnexeSection from '../AnnexeSection.vue'
import * as api from '../../services/api'

vi.mock('../../services/api', () => ({
  patchAnnexe: vi.fn().mockResolvedValue({ ok: true }),
}))

function mountSection(overrides: Record<string, unknown> = {}) {
  setActivePinia(createPinia())
  return mount(AnnexeSection, {
    props: {
      rid: '123',
      annexe: { address: 'FLAT 2, 2 WILLOWMEAD GARDENS', band: 'A', yearly_cost: { value: { amount: '900', currency: 'GBP' } } },
      payers: [],
      ignored: false,
      adults: [{ name: 'Simon' }, { name: 'Lorena' }, { name: 'Ashby' }],
      ...overrides,
    },
  })
}

describe('AnnexeSection', () => {
  it('shows the annexe, its separate council tax, and the payer pickers', () => {
    const wrapper = mountSection()
    const text = wrapper.text()
    expect(text).toContain('FLAT 2, 2 WILLOWMEAD GARDENS')
    expect(text).toContain('separate council tax')
    expect(text).toContain('Band A')
    expect(text).toContain('not yet included')
    expect(wrapper.findAll('input[type="checkbox"]')).toHaveLength(3)
  })

  it('saves the picked payers', async () => {
    const wrapper = mountSection()
    const boxes = wrapper.findAll('input[type="checkbox"]')
    await boxes[2].setValue(true) // Ashby
    await wrapper.find('.btn--primary').trigger('click')
    expect(api.patchAnnexe).toHaveBeenCalledWith('123', { payers: ['Ashby'], ignored: false })
  })

  it('hides the annexe when the user says it is not related, and can restore it', async () => {
    const wrapper = mountSection()
    await wrapper.find('.btn--secondary').trigger('click')
    expect(api.patchAnnexe).toHaveBeenCalledWith('123', { payers: [], ignored: true })
  })
})
