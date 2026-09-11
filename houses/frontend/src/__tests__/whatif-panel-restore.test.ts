import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import WhatIfPanel from '../components/WhatIfPanel.vue'
import { usePropertiesStore } from '../stores/properties'
import {
  applyWhatIf,
  fetchSettings,
  fetchWhatIfState,
  restoreWhatIf,
} from '../services/api'

vi.mock('../services/api', () => ({
  fetchSettings: vi.fn(),
  fetchWhatIfState: vi.fn(),
  applyWhatIf: vi.fn(),
  restoreWhatIf: vi.fn(),
  acceptWhatIf: vi.fn(),
  fetchAllSummaries: vi.fn().mockResolvedValue({}),
  fetchPropertyDetail: vi.fn().mockResolvedValue(null),
  patchTriage: vi.fn(),
}))

const _HOME = { amount: '550000', currency: 'GBP' }
const _MORTGAGE = { amount: '373000', currency: 'GBP' }

function settingsPayload(pimlicoTrips: number) {
  return {
    persons: {
      value: [
        {
          name: 'Simon',
          selling_home: true,
          has_car: true,
          home_sale_price: _HOME,
          outstanding_mortgage: _MORTGAGE,
          home_co_owners: [{ name: 'Lorena', share: 50 }],
          places_of_interest: [
            { label: 'Pimlico', address: 'Pimlico Rd, London', trips_per_week: pimlicoTrips, weeks_per_year: 46, acceptable_modes: ['car'] },
            { label: 'Bracknell', address: 'Broad Lane, Bracknell', trips_per_week: 1, weeks_per_year: 46, acceptable_modes: ['car'] },
          ],
        },
      ],
    },
  }
}

describe('WhatIfPanel restore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(fetchSettings).mockImplementation(async () => settingsPayload(1))
    vi.mocked(fetchWhatIfState).mockResolvedValue(false)
    vi.mocked(applyWhatIf).mockResolvedValue()
  })

  async function openPanelWithPimlicoEditedToZero() {
    const wrapper = mount(WhatIfPanel, { global: { plugins: [createPinia()] } })
    await flushPromises()
    usePropertiesStore()
    // Unfurl: the panel body (and its inputs) only render expanded,
    // and unfurling re-reads the server state.
    await wrapper.findAll('button').find(b => b.text().includes('What if'))!.trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text().trim() === 'Commutes')!.trigger('click')
    await flushPromises()
    const pim = wrapper.findAll('input[type="number"]').find(i =>
      (i.element.closest('label')?.textContent ?? '').includes('Pimlico'),
    )
    expect(pim).toBeDefined()
    await pim!.setValue(0)
    await wrapper.find('button.whatif__btn--primary').trigger('click')
    await flushPromises()
    expect(applyWhatIf).toHaveBeenCalledTimes(1)
    return { wrapper, pim: pim! }
  }

  it('reloads the real numbers into the fields after restoring', async () => {
    const { wrapper, pim } = await openPanelWithPimlicoEditedToZero()
    expect((pim.element as HTMLInputElement).value).toBe('0')

    await wrapper.find('button.whatif__btn--ghost').trigger('click')
    await flushPromises()

    expect(restoreWhatIf).toHaveBeenCalledTimes(1)
    // THE CONTRACT: after restoring, the panel is an editor of LIVE
    // data again — its fields must show the REAL numbers (1 day), not
    // the discarded scenario values.
    expect((pim.element as HTMLInputElement).value).toBe('1')
  })

  it('reverts the fields when another device restores', async () => {
    const { pim } = await openPanelWithPimlicoEditedToZero()

    // Another device restored: the websocket flips the mode flag and
    // the settings refresh lands — the panel must re-read the server.
    vi.mocked(fetchSettings).mockClear()
    usePropertiesStore().setWhatIfActive(false)
    await flushPromises()

    expect(fetchSettings).toHaveBeenCalled()
    expect((pim.element as HTMLInputElement).value).toBe('1')
  })
})
