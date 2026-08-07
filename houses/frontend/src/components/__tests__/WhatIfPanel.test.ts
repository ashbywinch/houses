import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { mount } from '@vue/test-utils'
import WhatIfPanel from '../WhatIfPanel.vue'
import { usePropertiesStore } from '../../stores/properties'

vi.mock('../../services/api', () => ({
  fetchSettings: vi.fn(),
  postWhatIf: vi.fn(),
  patchPerson: vi.fn().mockResolvedValue(new Response()),
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

function mountPanel() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(WhatIfPanel, { global: { plugins: [pinia] } })
  return { wrapper, store: usePropertiesStore() }
}

/** The panel is collapsed by default — open it before interacting. */
async function expand(wrapper: ReturnType<typeof mount>) {
  await wrapper.find('.whatif__toggle').trigger('click')
}

/** Flush microtasks (onMounted load, async run) without wall-clock time. */
async function settle() {
  await vi.advanceTimersByTimeAsync(0)
}

/** Fire the 400ms debounce deterministically and flush its async body. */
async function runDebouncedEval() {
  await vi.advanceTimersByTimeAsync(500)
}

describe('WhatIfPanel', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(api.fetchSettings).mockResolvedValue(settingsPersons as unknown as Record<string, unknown>)
    vi.mocked(api.postWhatIf).mockResolvedValue({
      'prop-a': { succeeded: true, monthly_total: { value: { amount: '900', currency: 'GBP' }, stddev: 0 } },
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders the family and the editable money fields', async () => {
    const { wrapper } = mountPanel()
    await settle()
    expect(wrapper.text()).toContain('What if…')
    await expand(wrapper)
    expect(wrapper.text()).toContain('Simon')
    expect(wrapper.text()).toContain('Ashby')
    expect(wrapper.text()).toContain('Expected sale price (£)')
    // nothing is "saved" until the user asks — badge hidden initially
    expect(wrapper.text()).not.toContain('not saved')
  })

  it('never shows children — they have no finances', async () => {
    const { wrapper } = mountPanel()
    await settle()
    await expand(wrapper)
    expect(wrapper.text()).not.toContain('George')
  })

  it('rejects pence as typed (whole pounds only)', async () => {
    const { wrapper } = mountPanel()
    await settle()
    await expand(wrapper)
    const sale = wrapper.findAll('.whatif-person__field input')[0]
    await sale.setValue('550000.50')
    expect((sale.element as HTMLInputElement).value).toBe('550000')
  })

  it('displays money as integer pounds (no decimals)', async () => {
    const { wrapper } = mountPanel()
    await settle()
    await expand(wrapper)
    // Simon's sale price is 550000.49 in the fixture → shown as 550000
    const sale = wrapper.findAll('.whatif-person__field input')[0]
    expect((sale.element as HTMLInputElement).value).toBe('550000')
  })

  it('runs the what-if on edit and marks it not saved', async () => {
    const { wrapper } = mountPanel()
    await settle()
    await expand(wrapper)

    // Ashby is not selling → her cash field carries the 'Cash available' label
    const ashbyCash = wrapper
      .findAll('.whatif-person__field')
      .find(l => l.text().includes('Cash available for the deposit'))!
      .find('input')
    await ashbyCash.setValue('400000')

    await runDebouncedEval()
    expect(api.postWhatIf).toHaveBeenCalled()
    const payload = vi.mocked(api.postWhatIf).mock.calls[0][0]
    const ashby = payload.find((p: Record<string, unknown>) => p.name === 'Ashby')
    expect(ashby?.cash_contribution).toEqual({ amount: '400000', currency: 'GBP' })

    await settle()
    expect(wrapper.text()).toContain('not saved')
  })

  it('shows a delta headline against real totals', async () => {
    const { wrapper, store } = mountPanel()
    await settle()
    await expand(wrapper)
    store.rids = ['prop-a']
    store.summaries = {
      'prop-a': {
        rid: 'prop-a',
        best_address: { succeeded: true, value: '10 Cheap St', error: null, provenance: { label: 't' } },
        best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 't' } },
        rightmove_price: { succeeded: true, value: { amount: '200000', currency: 'GBP' }, error: null, provenance: { label: 't' } },
        rightmove_bedrooms: { succeeded: true, value: '2', error: null, provenance: { label: 't' } },
        total_monthly_cost: { succeeded: true, value: { value: { amount: '1600', currency: 'GBP' }, stddev: 0 }, error: null, provenance: { label: 't' } },
        walkability: { succeeded: false, value: null, error: null, provenance: { label: 't' } },
        commutes: {},
        schools: {
          primary: { school: { succeeded: false, value: null, error: null, provenance: { label: 't' } } },
          secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 't' } } },
        },
      },
    }
    await settle()

    const cashInputs = wrapper.findAll('.whatif-person__field input')
    await cashInputs[cashInputs.length - 1].setValue('400000')
    await runDebouncedEval()
    await settle()

    // real 1600 is NOT under 1500, hypothetical 900 IS → one more house
    expect(wrapper.text()).toContain('1 more house under £1,500/mo')
  })

  it('applies the numbers to the family settings and clears the panel', async () => {
    const { wrapper } = mountPanel()
    await settle()
    await expand(wrapper)

    const cashInputs = wrapper.findAll('.whatif-person__field input')
    await cashInputs[cashInputs.length - 1].setValue('400000')
    await runDebouncedEval()
    await settle()
    expect(wrapper.text()).toContain('not saved')

    await wrapper.findAll('button').find(b => b.text().includes('Use these numbers'))!.trigger('click')
    await settle()

    expect(api.patchPerson).toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('not saved')
  })

  it('backs out to real numbers without saving', async () => {
    const { wrapper, store } = mountPanel()
    await settle()
    await expand(wrapper)

    store.applyWhatIf({ 'prop-a': { succeeded: true, monthly_total: { value: { amount: '900', currency: 'GBP' }, stddev: 0 } } })
    await settle()
    expect(wrapper.text()).toContain('not saved')

    await wrapper.findAll('button').find(b => b.text().includes('Back to real numbers'))!.trigger('click')
    await settle()
    expect(store.whatIfTotals).toBeNull()
    expect(wrapper.text()).not.toContain('not saved')
  })
})

describe('WhatIfPanel — commute tab (MPG + max walk)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(api.fetchSettings).mockResolvedValue(settingsPersons as unknown as Record<string, unknown>)
    vi.mocked(api.postWhatIf).mockResolvedValue({
      'prop-a': { succeeded: true, monthly_total: { value: { amount: '900', currency: 'GBP' }, stddev: 0 } },
    })
  })

  it('sends the car MPG and max-walk in the what-if payload and commits them', async () => {
    const { wrapper } = mountPanel()
    await settle()
    await expand(wrapper)
    // switch to the Commutes tab
    await wrapper.findAll('.settings-tabs button')[1].trigger('click')
    await wrapper.vm.$nextTick()
    const simon = wrapper.findAll('.whatif-person')[0]
    const mpg = simon.find('input[type="number"]')
    await mpg.setValue(38)
    const mw = wrapper.find('input[type="number"][min="0"]')
    await mw.setValue(25)
    await runDebouncedEval()
    expect(api.postWhatIf).toHaveBeenCalled()
    const body = (api.postWhatIf as ReturnType<typeof vi.fn>).mock.calls.at(-1)![0] as Array<Record<string, unknown>>
    const simonBody = body.find((b: Record<string, unknown>) => b.name === 'Simon')
    expect(simonBody?.petrol_mpg).toBe(38)
    expect(simonBody?.bus_walk_penalty).toEqual({ value: 25, unit: 'minute' })
    // Ashby keeps her own values
    const ashbyBody = body.find((b: Record<string, unknown>) => b.name === 'Ashby')
    expect(ashbyBody?.bus_walk_penalty).toEqual({ value: 15, unit: 'minute' })
  })
})
