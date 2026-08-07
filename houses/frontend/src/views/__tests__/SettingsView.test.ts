import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import SettingsView from '../SettingsView.vue'

vi.mock('../../services/api', () => ({
  fetchSettings: vi.fn(),
  patchPerson: vi.fn().mockResolvedValue({ status: 'ok' }),
  patchFinancial: vi.fn().mockResolvedValue({ status: 'ok' }),
}))

import * as api from '../../services/api'

function makeSettings() {
  return {
    persons: {
      status: 'succeeded',
      value: [
        {
          name: 'Simon',
          has_car: true,
          is_child: false,
          email: 'simon@example.com',
          is_superuser: true,
          editable_by: ['Simon'],
          editable_by_me: true,
          selling_home: true,
          home_sale_price: { amount: '550000.00', currency: 'GBP' },
          outstanding_mortgage: { amount: '373000.00', currency: 'GBP' },
          cash_contribution: { amount: '0.00', currency: 'GBP' },
          petrol_mpg: 45,
          bus_walk_penalty: { value: 20, unit: 'minute' },
          places_of_interest: [
            {
              label: 'Pimlico',
              address: '1 Drummond Gate, Pimlico, London SW1V 2QQ',
              trips_per_week: 1,
              weeks_per_year: 46,
              acceptable_modes: ['transit'],
            },
            {
              label: 'Bracknell',
              address: 'Waite House, Doncastle Road, Bracknell, Berkshire RG12 8YA',
              trips_per_week: 1,
              weeks_per_year: 46,
              acceptable_modes: ['car'],
            },
          ],
        },
        {
          name: 'Lorena',
          has_car: false,
          is_child: false,
          email: 'lorena@example.com',
          is_superuser: false,
          editable_by: ['Lorena'],
          editable_by_me: false,
          places_of_interest: [
            {
              label: 'Aldgate',
              address: 'Eastgate House, 40 Dukes Place, London EC3A 7LP',
              trips_per_week: 2,
              weeks_per_year: 46,
              acceptable_modes: ['transit'],
            },
          ],
        },
        {
          name: 'Ashby',
          has_car: true,
          is_child: false,
          email: 'emily.winch@gmail.com',
          is_superuser: false,
          editable_by: ['Ashby'],
          editable_by_me: true,
          selling_home: false,
          cash_contribution: { amount: '300000.00', currency: 'GBP' },
          places_of_interest: [],
        },
        {
          name: 'George',
          has_car: false,
          is_child: true,
          email: '',
          is_superuser: false,
          editable_by: ['Simon', 'Lorena'],
          editable_by_me: true,
          places_of_interest: [
            {
              label: 'Primary School',
              address: '',
              trips_per_week: 5,
              weeks_per_year: 39,
              acceptable_modes: ['walk'],
            },
          ],
        },
      ],
    },
    commute_thresholds: {
      status: 'succeeded',
      value: {
        Simon: { good_max_minutes: 30, fine_max_minutes: 45 },
        Lorena: { good_max_minutes: 40, fine_max_minutes: 60 },
      },
    },
    financial: {
      status: 'succeeded',
      value: {
        mortgage_rate: 0.0495,
        mortgage_term_years: 27,
        sinking_fund_rate: 0.01,
        petrol_mpg: 45,
        petrol_cost_per_litre: 1.45,
      },
    },
    household_deposit: {
      total: { amount: '477000.00', currency: 'GBP' },
      persons: {
        Simon: { amount: '177000.00', currency: 'GBP' },
        Lorena: { amount: '0.00', currency: 'GBP' },
        George: { amount: '0.00', currency: 'GBP' },
      },
      provenance: {
        label: 'Household Deposit',
        value: '£477,000.00',
        sourceType: 'calc' as const,
        formula: {
          lines: [
            { label: 'Simon', value: '£550,000.00 sale − £373,000.00 mortgage + £0.00 cash = £177,000.00' },
            { label: 'Ashby', value: '£0 home + £300,000.00 cash = £300,000.00' },
          ],
          result: '£477,000.00',
        },
      },
    },
  }
}

async function mountView(query = '', personName = 'Simon') {
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.user = { email: `${personName.toLowerCase()}@example.com`, name: personName, picture: '', person: personName, is_superuser: personName === 'Simon' } as any
  ;(api.fetchSettings as ReturnType<typeof vi.fn>).mockResolvedValue(makeSettings())
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/settings', component: SettingsView }],
  })
  await router.push('/settings' + query)
  await router.isReady()
  return { wrapper: mount(SettingsView, { global: { plugins: [router] } }), flush: flushPromises, router }
}

import type { VueWrapper } from '@vue/test-utils'

/** The person identity strip (name, badges, autosave status) — visible on both tabs. */
const strip = (wrapper: VueWrapper) => wrapper.find('.settings-person__strip')

/** The visible panel's person section (money on Finances, commutes on Commutes). */
const personSection = (wrapper: VueWrapper) => wrapper.find('.settings-panel .settings-person')

/** Switch to the Commutes tab (destinations, bands, has-car live there). */
async function showCommutes(wrapper: VueWrapper) {
  await wrapper.findAll('.settings-tabs button')[1].trigger('click')
  await wrapper.vm.$nextTick()
}

describe('SettingsView — family sections', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders ONLY the session person's settings — not the whole family's", async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const sections = wrapper.findAll('.settings-panel .settings-person')
    expect(sections.length).toBe(1)
    expect(strip(wrapper).text()).toContain('Simon')
    expect(sections[0].text()).not.toContain('Lorena')
    expect(sections[0].text()).not.toContain('George')
    // the deposit breakdown still shows the whole household
    expect(wrapper.text()).toContain('Total deposit from everyone')
  })

  it('opens on the Finances tab (left) by default', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const tabs = wrapper.findAll('.settings-tabs button')
    expect(tabs[0].text()).toBe('Finances')
    expect(tabs[1].text()).toBe('Commutes')
    expect(tabs[0].attributes('aria-selected')).toBe('true')
    expect(wrapper.text()).toContain('Total deposit from everyone')
    // commutes content is not rendered until the tab is opened
    expect(wrapper.find('input#has-car').exists()).toBe(false)
  })

  it('marks the session person with a "you" badge, or "child" for a child user', async () => {
    const simon = await mountView()
    await simon.flush()
    expect(strip(simon.wrapper).text()).toContain('you')
    expect(strip(simon.wrapper).text()).not.toContain('child')

    const george = await mountView('', 'George')
    await george.flush()
    expect(strip(george.wrapper).text()).toContain('child')
  })

  it('renders the school note for a child with no-address POIs', async () => {
    const { wrapper, flush } = await mountView('', 'George')
    await flush()
    await showCommutes(wrapper)
    expect(wrapper.text()).toContain('Goes to school near the house')
  })
})

describe('SettingsView — ownership rendering', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('makes the own person editable — autosave, no save button', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    await showCommutes(wrapper)
    const simonSection = personSection(wrapper)
    expect(simonSection.find('button.save').exists()).toBe(false)
    expect(simonSection.find('input[type="checkbox"][data-mode="walk"]').attributes('disabled')).toBeUndefined()
  })

  it('shows a person without edit rights as read-only — no save button', async () => {
    const { wrapper, flush } = await mountView('', 'Lorena')
    await flush()
    expect(strip(wrapper).text()).toContain('read-only')
    expect(wrapper.find('button.save').exists()).toBe(false)
  })

  it('does not offer the car mode to people without a car', async () => {
    const lorena = await mountView('', 'Lorena')
    await lorena.flush()
    await showCommutes(lorena.wrapper)
    const lorenaSection = personSection(lorena.wrapper)
    // the checkbox is present but hidden and disabled — car is not an option
    const carInput = lorenaSection.find('input[type="checkbox"][data-mode="car"]')
    expect(carInput.exists()).toBe(true)
    expect(carInput.attributes('disabled')).toBeDefined()
    expect(lorenaSection.find('.settings-poi__mode--hidden input[data-mode="car"]').exists()).toBe(true)

    const simon = await mountView()
    await simon.flush()
    await showCommutes(simon.wrapper)
    const simonSection = personSection(simon.wrapper)
    expect(simonSection.find('.settings-poi__mode--hidden input[data-mode="car"]').exists()).toBe(false)
  })
})

describe('SettingsView — household finances', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the live household-finance assumptions with converted display values', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const text = wrapper.text()
    expect(text).toContain('Household finances')
    // stored rates (0.0495, 0.01) display as percentages (4.95, 1)
    expect((wrapper.find('input#mortgage-rate').element as HTMLInputElement).value).toBe('4.95')
    expect((wrapper.find('input#mortgage-term').element as HTMLInputElement).value).toBe('27')
    expect((wrapper.find('input#sinking-fund').element as HTMLInputElement).value).toBe('1')
    expect((wrapper.find('input#petrol-cost').element as HTMLInputElement).value).toBe('1.45')
    // MPG moved out of the household finances — it's the car owner's own, on the Commutes tab
    expect(wrapper.find('input#petrol-mpg').exists()).toBe(false)
  })

  it('saves financial edits back as stored values (percent → fraction)', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    await wrapper.find('input#mortgage-rate').setValue('6')
    await wrapper.find('input#sinking-fund').setValue('1.5')
    await wrapper.find('input#petrol-cost').setValue('1.50')
    await wrapper.find('.settings-finances').trigger('focusout')
    await flush()
    expect(api.patchFinancial).toHaveBeenCalledTimes(1)
    const body = (api.patchFinancial as ReturnType<typeof vi.fn>).mock.calls[0][0]
    expect(body.mortgage_rate).toBe(0.06)
    expect(body.sinking_fund_rate).toBe(0.015)
    expect(body.petrol_cost_per_litre).toBe(1.5)
    expect(body.petrol_mpg).toBeUndefined()
    expect(body.mortgage_term_years).toBe(27)
  })
})

describe('SettingsView — deposit summary and money labels', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the household deposit as one number with a breakdown', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const text = wrapper.text()
    expect(text).toContain('Total deposit from everyone')
    expect(text).toContain('477,000')
    expect(text).toContain('177,000')
  })

  it('labels the money fields unambiguously', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const text = wrapper.text()
    expect(text).toContain('Expected sale price of current home')
    expect(text).toContain('Mortgage remaining on current home')
    expect(text).toContain('Other money toward the deposit')
  })
})

describe('SettingsView — saving', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('saves the edited person and their thresholds via autosave', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    await showCommutes(wrapper)
    const simonSection = personSection(wrapper)
    // tick a mode checkbox — blurring the section autosaves
    const walk = simonSection.find('input[type="checkbox"][data-mode="walk"]')
    await walk.setValue(true)
    await simonSection.trigger('focusout')
    await flush()
    expect(api.patchPerson).toHaveBeenCalledTimes(1)
    const [name, body] = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(name).toBe('Simon')
    const pimlico = body.places_of_interest.find((p: { label: string }) => p.label === 'Pimlico')
    expect(pimlico.acceptable_modes).toContain('walk')
    expect(body.thresholds).toEqual({ good_max_minutes: 30, fine_max_minutes: 45 })
  })

  it('saves the commute colour bands — good AND fine — when they change', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    await showCommutes(wrapper)
    const simon = personSection(wrapper)
    expect(simon.find('input#good-max').exists()).toBe(true)
    await simon.find('input#good-max').setValue(35)
    await simon.find('input#fine-max').setValue(50)
    await simon.trigger('focusout')
    await flush()
    const [, body] = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(body.thresholds).toEqual({ good_max_minutes: 35, fine_max_minutes: 50 })
  })

  it('saves the max-walk setting with the person', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    await showCommutes(wrapper)
    const simon = personSection(wrapper)
    expect((simon.find('input#max-walk').element as HTMLInputElement).value).toBe('20')
    await simon.find('input#max-walk').setValue(25)
    await simon.trigger('focusout')
    await flush()
    const [, body] = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(body.bus_walk_penalty).toEqual({ value: 25, unit: 'minute' })
  })

  it('shows the car MPG under has-a-car on the Commutes tab and saves it', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    await showCommutes(wrapper)
    const simon = personSection(wrapper)
    expect((simon.find('input#petrol-mpg').element as HTMLInputElement).value).toBe('45')
    await simon.find('input#petrol-mpg').setValue(38)
    await simon.trigger('focusout')
    await flush()
    const [, body] = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(body.petrol_mpg).toBe(38)
  })

  it('saves the destination weeks per year', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    await showCommutes(wrapper)
    const simon = personSection(wrapper)
    const weeks = simon.findAll('input[id^="weeks-"]')[0]
    expect(weeks.exists()).toBe(true)
    await weeks.setValue(48)
    await simon.trigger('focusout')
    await flush()
    const [, body] = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(body.places_of_interest[0].weeks_per_year).toBe(48)
  })
})

describe('SettingsView — destination fields and person-scroll (A6, D2)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('distinguishes the destination name from its address', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    await showCommutes(wrapper)
    const text = wrapper.text()
    expect(text).toContain('Destination name')
    expect(text).toContain('Address')
    // the 'office' wording is gone — destinations aren't necessarily offices
    expect(text).not.toContain('Office / location address')
  })

  it('highlights and scrolls to the session person named in the URL', async () => {
    const { wrapper, flush } = await mountView('?person=Simon')
    await flush()
    expect(strip(wrapper).classes()).toContain('settings-person--target')
  })
})

describe('SettingsView — selling-home toggle (P7, B7)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the current-home fields when a home is being sold', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const simon = personSection(wrapper)
    expect(simon.text()).toContain('I am selling a home to fund this purchase')
    expect(simon.text()).toContain('Expected sale price of current home')
    expect(simon.text()).toContain('Mortgage remaining on current home')
  })

  it('hides the current-home fields for a cash-only person and relabels the deposit', async () => {
    const { wrapper, flush } = await mountView('', 'Ashby')
    await flush()
    const ashby = personSection(wrapper)
    expect(ashby.text()).not.toContain('Expected sale price of current home')
    expect(ashby.text()).not.toContain('Mortgage remaining on current home')
    expect(ashby.text()).toContain('Cash available for the deposit')
    expect(ashby.text()).toContain('Deposit is cash')
  })
})

describe('SettingsView — deposit provenance through the standard toggle (P8)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('reveals the per-person deposit calculation via the standard component', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const toggle = wrapper.find('.settings-deposit .provenance-toggle__trigger')
    expect(toggle.exists()).toBe(true)
    expect(toggle.text()).toContain('How is this calculated?')
    await toggle.trigger('click')
    expect(wrapper.text()).toContain('£0 home + £300,000.00 cash')
    expect(wrapper.text()).toContain('£550,000.00 sale')
  })
})

describe('SettingsView — commute destination CRUD (A7)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('adds a blank destination row and saves it with the list', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    await showCommutes(wrapper)
    const simon = personSection(wrapper)
    await simon.find('button.poi-add').trigger('click')
    await flush()
    const rows = simon.findAll('.settings-poi')
    expect(rows.length).toBe(3)  // Pimlico, Bracknell + the new blank row
    await simon.trigger('focusout')  // blur autosaves the added row
    await flush()
    const [name, body] = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(name).toBe('Simon')
    expect(body.places_of_interest.length).toBe(3)
    expect(body.places_of_interest[2].label).toBe('')
  })

  it('defaults a new destination to explicit modes matching the person', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    await showCommutes(wrapper)
    const simon = personSection(wrapper)
    await simon.find('button.poi-add').trigger('click')
    await simon.trigger('focusout')
    await flush()
    const [, body] = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0]
    const added = body.places_of_interest[body.places_of_interest.length - 1]
    expect(added.acceptable_modes).toEqual(['transit', 'car', 'walk'])
  })

  it('removes a destination row', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    await showCommutes(wrapper)
    const simon = personSection(wrapper)
    expect(simon.findAll('.settings-poi').length).toBe(2)
    await simon.findAll('button.poi-remove')[0].trigger('click')
    await flush()
    expect(simon.findAll('.settings-poi').length).toBe(1)
  })
})

describe('SettingsView — selling-home persists on save (B7)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('sends the selling-home toggle in the save body', async () => {
    const { wrapper, flush } = await mountView('', 'Ashby')
    await flush()
    const ashby = personSection(wrapper)
    await ashby.find('input#selling-home').setValue(true)
    await ashby.trigger('focusout')
    await flush()
    const [name, body] = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(name).toBe('Ashby')
    expect(body.selling_home).toBe(true)
  })
})

describe('SettingsView — acceptable modes keep at least one (P7)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('cannot uncheck the last remaining mode', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    await showCommutes(wrapper)
    const simon = personSection(wrapper)
    // Pimlico has only ['transit'] — unchecking it must not remove the last
    // mode (an empty set would be reinterpreted by the server migration)
    const transit = simon.find('input[type="checkbox"][data-mode="transit"]')
    await transit.setValue(false)
    await simon.trigger('focusout')
    await flush()
    const [name, body] = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(name).toBe('Simon')
    const pimlico = body.places_of_interest.find((p: { label: string }) => p.label === 'Pimlico')
    expect(pimlico.acceptable_modes).toContain('transit')
  })
})

describe('SettingsView — autosave status and undo (C2/C3)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows "Saved ✓" with an Undo action after an autosave', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const simon = personSection(wrapper)
    await simon.find('input#cash').setValue('5000')
    await simon.trigger('focusout')
    await flush()
    expect(wrapper.text()).toContain('Saved ✓')
    expect(strip(wrapper).find('.settings-person__undo').text()).toContain('Undo')
  })

  it('undo re-patches the previous snapshot', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const simon = personSection(wrapper)
    await simon.find('input#cash').setValue('5000')
    await simon.trigger('focusout')
    await flush()
    const savedBody = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0][1]
    await strip(wrapper).find('.settings-person__undo').trigger('click')
    await flush()
    expect(api.patchPerson).toHaveBeenCalledTimes(2)
    expect((api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[1][1]).toEqual(savedBody)
  })

  it('autosaves on the debounce without waiting for blur', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    vi.useFakeTimers()
    const simon = personSection(wrapper)
    await simon.find('input#cash').setValue('5000')
    await vi.advanceTimersByTimeAsync(900)
    await flush()
    expect(api.patchPerson).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })

  it('shows an error state with Retry when the save fails', async () => {
    vi.mocked(api.patchPerson).mockRejectedValueOnce(new Error('boom'))
    const { wrapper, flush } = await mountView()
    await flush()
    const simon = personSection(wrapper)
    await simon.find('input#cash').setValue('5000')
    await simon.trigger('focusout')
    await flush()
    expect(wrapper.text()).toContain("Couldn't save")
    expect(strip(wrapper).find('.settings-person__undo').text()).toContain('Retry')
  })
})

describe('SettingsView — empty money inputs normalize on save', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('sends "0" for a cleared money field instead of failing the save', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const simon = personSection(wrapper)
    const sale = simon.find('input#home-sale')
    await sale.setValue('')
    await simon.trigger('focusout')
    await flush()
    const [, body] = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(body.home_sale_price.amount).toBe('0')
  })

  it('rejects pence in the large money fields (whole pounds only)', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const simon = personSection(wrapper)
    const sale = simon.find('input#home-sale')
    await sale.setValue('550000.99')
    expect((sale.element as HTMLInputElement).value).toBe('550000')
    await simon.trigger('focusout')
    await flush()
    const [, body] = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(body.home_sale_price.amount).toBe('550000')
  })

  it('blocks the decimal key outright on whole-pound fields', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const simon = personSection(wrapper)
    const sale = simon.find('input#home-sale')
    await sale.setValue('550000')
    const evt = new KeyboardEvent('keydown', { key: '.', cancelable: true })
    sale.element.dispatchEvent(evt)
    expect(evt.defaultPrevented).toBe(true)
    expect((sale.element as HTMLInputElement).value).toBe('550000')
  })
})
