import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import AnnexeSection from '../AnnexeSection.vue'
import * as api from '../../services/api'

vi.mock('../../services/api', () => ({
  patchCouncilTax: vi.fn().mockResolvedValue({ ok: true }),
}))

function mountSection(overrides: Record<string, unknown> = {}) {
  setActivePinia(createPinia())
  return mount(AnnexeSection, {
    props: {
      rid: '123',
      mainBill: { band: 'D', yearly_cost: { value: { amount: '1800', currency: 'GBP' } } },
      annexe: { address: 'FLAT 2, 2 WILLOWMEAD GARDENS', band: 'A', yearly_cost: { value: { amount: '900', currency: 'GBP' } } },
      mainPayers: [],
      annexePayers: [],
      ignored: false,
      adults: [{ name: 'Simon' }, { name: 'Lorena' }, { name: 'Ashby' }],
      ...overrides,
    },
  })
}

describe('AnnexeSection council-tax apportionment', () => {
  it('shows both bills and says the main bill defaults to all adults', () => {
    const wrapper = mountSection()
    const text = wrapper.text()
    expect(text).toContain('Main house — Band D')
    expect(text).toContain('Simon, Lorena, Ashby')
    expect(text).toContain('all adults — default')
    expect(text).toContain('FLAT 2, 2 WILLOWMEAD GARDENS')
    expect(text).toContain('separate council tax')
    expect(text).toContain('Not yet included in the monthly costs')
    expect(wrapper.findAll('input[type="checkbox"]')).toHaveLength(6)
  })

  it('saves the picked payers for both bills', async () => {
    const wrapper = mountSection()
    const boxes = wrapper.findAll('input[type="checkbox"]')
    await boxes[0].setValue(true) // Simon pays the main bill
    await boxes[5].setValue(true) // Ashby pays the annexe bill
    await wrapper.find('.btn--primary').trigger('click')
    expect(api.patchCouncilTax).toHaveBeenCalledWith('123', {
      main_payers: ['Simon'],
      annexe_payers: ['Ashby'],
      ignored: false,
    })
  })

  it('hides the annexe when the user says it is not related', async () => {
    const wrapper = mountSection()
    await wrapper.find('.ctax-hide').trigger('click')
    expect(api.patchCouncilTax).toHaveBeenCalledWith('123', {
      main_payers: [],
      annexe_payers: [],
      ignored: true,
    })
  })
})
