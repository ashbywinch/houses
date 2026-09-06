import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import WhatIfPanel from '../WhatIfPanel.vue'
import { usePropertiesStore } from '../../stores/properties'

vi.mock('../../services/api', () => ({
  fetchSettings: vi.fn(),
  fetchWhatIfState: vi.fn().mockResolvedValue(false),
  applyWhatIf: vi.fn().mockResolvedValue(undefined),
  restoreWhatIf: vi.fn().mockResolvedValue(undefined),
  acceptWhatIf: vi.fn().mockResolvedValue(undefined),
  fetchAllSummaries: vi.fn().mockResolvedValue({}),
}))

import * as api from '../../services/api'

const settingsPersons = {
  persons: {
    succeeded: true,
    value: [
      {
        name: 'Simon',
        selling_home: true,
        has_car: true,
        petrol_mpg: 45,
        bus_walk_penalty: { value: 20, unit: 'minute' },
        home_sale_price: { amount: '550000.49', currency: 'GBP' },
        outstanding_mortgage: { amount: '373000', currency: 'GBP' },
        cash_contribution: { amount: '0', currency: 'GBP' },
        places_of_interest: [{ label: 'Pimlico', address: '1 Pimlico Rd', trips_per_week: 1, weeks_per_year: 46, acceptable_modes: ['transit'] }],
      },
      {
        name: 'Ashby',
        selling_home: false,
        has_car: false,
        bus_walk_penalty: { value: 15, unit: 'minute' },
        cash_contribution: { amount: '300000', currency: 'GBP' },
        places_of_interest: [],
      },
      {
        name: 'George',
        is_child: true,
        selling_home: false,
        cash_contribution: { amount: '0', currency: 'GBP' },
        places_of_interest: [{ label: 'Primary School', address: '', trips_per_week: 5, weeks_per_year: 39, acceptable_modes: ['walk'] }],
      },
    ],
  },
}

/** The mounted panel wrapper — named once so helpers stay honest. */
type PanelWrapper = VueWrapper<InstanceType<typeof WhatIfPanel>>

/** The panel is collapsed by default — open it before interacting. */
async function expand(wrapper: PanelWrapper) {
  await wrapper.find('.whatif__toggle').trigger('click')
}

function mountPanel() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(WhatIfPanel, { global: { plugins: [pinia] } })
  return { wrapper, store: usePropertiesStore() }
}


/** Mount + flush the onMounted persons/state load. */
async function mountOpenPanel() {
  const { wrapper, store } = mountPanel()
  await flushPromises()
  await expand(wrapper)
  await flushPromises()
  return { wrapper, store }
}

function findButton(wrapper: PanelWrapper, text: string) {
  const b = wrapper.findAll('button').find(b => b.text().includes(text))
  expect(b, `expected a "${text}" button`).toBeDefined()
  return b!
}

describe('WhatIfPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.fetchSettings).mockResolvedValue(settingsPersons as unknown as Record<string, unknown>)
    vi.mocked(api.fetchWhatIfState).mockResolvedValue(false)
  })

  it('renders the family and the editable money fields', async () => {
    const { wrapper } = await mountOpenPanel()
    expect(wrapper.text()).toContain('What if…')
    expect(wrapper.text()).toContain('Simon')
    expect(wrapper.text()).toContain('Ashby')
    expect(wrapper.text()).toContain('Expected sale price (£)')
  })

  it('explains that applying changes the saved numbers everywhere', async () => {
    const { wrapper } = await mountOpenPanel()
    expect(wrapper.text()).toContain('changes your saved numbers everywhere')
    expect(wrapper.text()).toContain('come back with one click')
    // The old "not saved" badge is gone — applying is what changes numbers.
    expect(wrapper.text()).not.toContain('not saved')
  })

  it('never shows children — they have no finances', async () => {
    const { wrapper } = await mountOpenPanel()
    expect(wrapper.text()).not.toContain('George')
  })

  it('rejects pence as typed (whole pounds only)', async () => {
    const { wrapper } = await mountOpenPanel()
    const sale = wrapper.findAll('.whatif-person__field input')[0]
    await sale.setValue('550000.50')
    expect((sale.element as HTMLInputElement).value).toBe('550000')
  })

  it('displays money as integer pounds (no decimals)', async () => {
    const { wrapper } = await mountOpenPanel()
    // Simon's sale price is 550000.49 in the fixture → shown as 550000
    const sale = wrapper.findAll('.whatif-person__field input')[0]
    expect((sale.element as HTMLInputElement).value).toBe('550000')
  })

  it('uses the common settings-card layout for person cards', async () => {
    // The What If person cards must reuse the standard card/heading/
    // toggle-row CSS — no inline margin bodges, no zeroed padding.
    const { wrapper } = await mountOpenPanel()
    const card = wrapper.find('.whatif-person')
    // card uses the common padding (not a bespoke 0 0.6rem override)
    const cardStyle = getComputedStyle(card.element)
    expect(cardStyle.paddingLeft).not.toBe('0px')
    expect(cardStyle.paddingRight).not.toBe('0px')
    // toggle-row is a SIBLING of card-heading (common pattern), not an
    // inline-styled child inside it
    const heading = card.find('.card-heading')
    expect(heading.exists()).toBe(true)
    const toggle = card.find('.toggle-row')
    expect(toggle.exists()).toBe(true)
    expect(toggle.element.parentElement).toBe(card.element)
    expect(toggle.element.getAttribute('style')).toBeNull()
  })

  it('furling an active what-if cancels it — the real numbers come back', async () => {
    vi.mocked(api.fetchWhatIfState).mockResolvedValue(true)
    const { wrapper, store } = mountPanel()
    await flushPromises()
    expect(store.whatIfActive).toBe(true)
    expect(wrapper.find('.whatif--collapsed').exists()).toBe(false)

    // the user furls the panel — that cancels the what-if
    const toggle = wrapper.find('.whatif__toggle')
    await toggle.trigger('click')
    await flushPromises()

    expect(api.restoreWhatIf).toHaveBeenCalledTimes(1)
    expect(store.whatIfActive).toBe(false)
    expect(wrapper.find('.whatif--collapsed').exists()).toBe(true)

    // unfurling again shows the inactive panel: Try scenario, editable fields
    await toggle.trigger('click')
    await flushPromises()
    expect(wrapper.find('.whatif--collapsed').exists()).toBe(false)
    expect(wrapper.text()).toContain('Try scenario')
  })

  it('locks the scenario fields and shows exactly two exits while active', async () => {
    vi.mocked(api.fetchWhatIfState).mockResolvedValue(true)
    const { wrapper } = await mountPanel()
    await flushPromises()

    const buttons = wrapper.findAll('.whatif__footer button')
    expect(buttons.map(b => b.text())).toEqual(['Back to real numbers', 'Keep these numbers'])

    const ashbyCash = wrapper
      .findAll('.whatif-person__field')
      .find(l => l.text().includes('Cash available for the deposit'))!
      .find('input')
    expect((ashbyCash.element as HTMLInputElement).matches(":disabled")).toBe(true)
  })

  it('offers only Try scenario when nothing is active — fields editable', async () => {
    const { wrapper } = await mountOpenPanel()

    const buttons = wrapper.findAll('.whatif__footer button')
    expect(buttons.map(b => b.text())).toEqual(['Try scenario'])

    const ashbyCash = wrapper
      .findAll('.whatif-person__field')
      .find(l => l.text().includes('Cash available for the deposit'))!
      .find('input')
    expect((ashbyCash.element as HTMLInputElement).disabled).toBe(false)
  })

  it('restores the real numbers from "Back to real numbers" and clears the flag', async () => {
    vi.mocked(api.fetchWhatIfState).mockResolvedValue(true)
    const { wrapper, store } = mountPanel()
    await flushPromises()

    await findButton(wrapper, 'Back to real numbers').trigger('click')
    await flushPromises()

    expect(api.restoreWhatIf).toHaveBeenCalledTimes(1)
    expect(store.whatIfActive).toBe(false)
  })

  it('keeps the scenario from "Keep these numbers" without restoring', async () => {
    vi.mocked(api.fetchWhatIfState).mockResolvedValue(true)
    const { wrapper, store } = mountPanel()
    await flushPromises()

    await findButton(wrapper, 'Keep these numbers').trigger('click')
    await flushPromises()

    expect(api.acceptWhatIf).toHaveBeenCalledTimes(1)
    expect(api.restoreWhatIf).not.toHaveBeenCalled()
    expect(store.whatIfActive).toBe(false)
  })

  it('makes NO api call when a field is edited — the auto-eval is gone', async () => {
    const { wrapper } = await mountOpenPanel()
    const settingsCalls = vi.mocked(api.fetchSettings).mock.calls.length

    const ashbyCash = wrapper
      .findAll('.whatif-person__field')
      .find(l => l.text().includes('Cash available for the deposit'))!
      .find('input')
    await ashbyCash.setValue('400000')
    await flushPromises()

    expect(api.applyWhatIf).not.toHaveBeenCalled()
    expect(api.restoreWhatIf).not.toHaveBeenCalled()
    // the panel loaded persons once and never re-fetched on input
    expect(vi.mocked(api.fetchSettings).mock.calls.length).toBe(settingsCalls)
  })

  it('applies the edited payload and flags the store on "Try scenario"', async () => {
    const { wrapper, store } = await mountOpenPanel()

    const ashbyCash = wrapper
      .findAll('.whatif-person__field')
      .find(l => l.text().includes('Cash available for the deposit'))!
      .find('input')
    await ashbyCash.setValue('400000')

    await findButton(wrapper, 'Try scenario').trigger('click')
    await flushPromises()

    expect(api.applyWhatIf).toHaveBeenCalledTimes(1)
    const payload = vi.mocked(api.applyWhatIf).mock.calls[0][0] as Array<Record<string, unknown>>
    const ashby = payload.find((p: Record<string, unknown>) => p.name === 'Ashby')
    expect(ashby?.cash_contribution).toEqual({ amount: '400000', currency: 'GBP' })
    // Simon's sale-price money is normalised to whole pounds in the payload
    const simon = payload.find((p: Record<string, unknown>) => p.name === 'Simon')
    expect(simon?.home_sale_price).toEqual({ amount: '550000', currency: 'GBP' })

    expect(store.whatIfActive).toBe(true)
  })

  it('shows an error line and keeps the flag when Apply fails', async () => {
    vi.mocked(api.applyWhatIf).mockRejectedValueOnce(new Error('500'))
    const { wrapper, store } = await mountOpenPanel()

    await findButton(wrapper, 'Try scenario').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain("Couldn't apply the what-if.")
    expect(store.whatIfActive).toBe(false)
  })

  it('offers only "Try scenario" in the footer when nothing is active', async () => {
    const { wrapper } = await mountOpenPanel()

    findButton(wrapper, 'Try scenario')
    expect(wrapper.findAll('button').some(b => b.text().includes('Back to real numbers'))).toBe(false)
    expect(wrapper.findAll('button').some(b => b.text().includes('Keep these numbers'))).toBe(false)
  })
})

describe('WhatIfPanel — commute tab (MPG + max walk)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.fetchSettings).mockResolvedValue(settingsPersons as unknown as Record<string, unknown>)
    vi.mocked(api.fetchWhatIfState).mockResolvedValue(false)
  })

  it('sends the car MPG and max-walk in the apply payload', async () => {
    const { wrapper } = await mountOpenPanel()
    // switch to the Commutes tab
    await wrapper.findAll('.settings-tabs button')[1].trigger('click')
    await wrapper.vm.$nextTick()
    const simon = wrapper.findAll('.whatif-person')[0]
    const mpg = simon.find('input[type="number"]')
    await mpg.setValue(38)
    const mw = wrapper.find('input[type="number"][min="0"]')
    await mw.setValue(25)

    await findButton(wrapper, 'Try scenario').trigger('click')
    await flushPromises()

    const body = vi.mocked(api.applyWhatIf).mock.calls[0][0] as Array<Record<string, unknown>>
    const simonBody = body.find((b: Record<string, unknown>) => b.name === 'Simon')
    expect(simonBody?.petrol_mpg).toBe(38)
    expect(simonBody?.bus_walk_penalty).toEqual({ value: 25, unit: 'minute' })
    // Ashby keeps her own values
    const ashbyBody = body.find((b: Record<string, unknown>) => b.name === 'Ashby')
    expect(ashbyBody?.bus_walk_penalty).toEqual({ value: 15, unit: 'minute' })
  })
})

describe('WhatIfPanel — has_car and empty money (reviewer findings)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.fetchSettings).mockResolvedValue(settingsPersons as unknown as Record<string, unknown>)
    vi.mocked(api.fetchWhatIfState).mockResolvedValue(false)
  })

  it('sends has_car in the apply payload so the toggle is not dead UI', async () => {
    const { wrapper } = await mountOpenPanel()
    await wrapper.findAll('.settings-tabs button')[1].trigger('click')
    await wrapper.vm.$nextTick()
    // Simon starts has_car=true; flip it off before applying
    const simon = wrapper.findAll('.whatif-person')[0]
    await simon.find('.switch').trigger('click')

    await findButton(wrapper, 'Try scenario').trigger('click')
    await flushPromises()

    const body = vi.mocked(api.applyWhatIf).mock.calls[0][0] as Array<Record<string, unknown>>
    const simonBody = body.find((b: Record<string, unknown>) => b.name === 'Simon')
    expect(simonBody?.has_car).toBe(false)
    const ashbyBody = body.find((b: Record<string, unknown>) => b.name === 'Ashby')
    expect(ashbyBody?.has_car).toBe(false)
  })

  it('normalises cleared whole-pound fields to 0 instead of sending empty', async () => {
    const { wrapper } = await mountOpenPanel()
    // Ashby's cash field is cleared → the payload must send '0', not ''
    // (the server rejects empty amounts with 400)
    const ashbyCash = wrapper
      .findAll('.whatif-person__field')
      .find(l => l.text().includes('Cash available for the deposit'))!
      .find('input')
    await ashbyCash.setValue('')

    await findButton(wrapper, 'Try scenario').trigger('click')
    await flushPromises()

    const body = vi.mocked(api.applyWhatIf).mock.calls[0][0] as Array<Record<string, unknown>>
    const ashbyBody = body.find((b: Record<string, unknown>) => b.name === 'Ashby')
    expect(ashbyBody?.cash_contribution).toEqual({ amount: '0', currency: 'GBP' })
  })
})
