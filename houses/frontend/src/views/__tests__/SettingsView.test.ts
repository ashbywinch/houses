import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import SettingsView from '../SettingsView.vue'

vi.mock('../../services/api', () => ({
  fetchSettings: vi.fn(),
  patchPerson: vi.fn().mockResolvedValue({ status: 'ok' }),
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
          places_of_interest: [
            {
              label: 'Pimlico',
              address: '1 Drummond Gate, Pimlico, London SW1V 2QQ',
              trips_per_week: 1,
              weeks_per_year: 46,
              acceptable_modes: ['train'],
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
              acceptable_modes: ['train'],
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

async function mountView(query = '') {
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.user = { email: 'simon@example.com', name: 'Simon', picture: '', person: 'Simon', is_superuser: true } as any
  ;(api.fetchSettings as ReturnType<typeof vi.fn>).mockResolvedValue(makeSettings())
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/settings', component: SettingsView }],
  })
  await router.push('/settings' + query)
  await router.isReady()
  return { wrapper: mount(SettingsView, { global: { plugins: [router] } }), flush: flushPromises, router }
}

describe('SettingsView — family sections', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders a section per person', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const text = wrapper.text()
    expect(text).toContain('Simon')
    expect(text).toContain('Lorena')
    expect(text).toContain('George')
  })

  it('marks the session person with a "you" badge and the child with a child badge', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    expect(wrapper.text()).toContain('you')
    expect(wrapper.text()).toContain('child')
  })

  it('renders the school note for a child with no-address POIs', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
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
    const simonSection = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
    expect(simonSection.find('button.save').exists()).toBe(false)
    expect(simonSection.find('input[type="checkbox"][data-mode="walk"]').attributes('disabled')).toBeUndefined()
  })

  it('locks other people — read-only, no save button', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const lorenaSection = wrapper.findAll('.settings-person').find(s => s.text().includes('Lorena'))!
    expect(lorenaSection.text()).toContain('read-only')
    expect(lorenaSection.find('button.save').exists()).toBe(false)
  })

  it('does not offer the car mode to people without a car', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const lorenaSection = wrapper.findAll('.settings-person').find(s => s.text().includes('Lorena'))!
    // the checkbox is present but hidden and disabled — car is not an option
    const carInput = lorenaSection.find('input[type="checkbox"][data-mode="car"]')
    expect(carInput.exists()).toBe(true)
    expect(carInput.attributes('disabled')).toBeDefined()
    expect(lorenaSection.find('.settings-poi__mode--hidden input[data-mode="car"]').exists()).toBe(true)
    const simonSection = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
    expect(simonSection.find('.settings-poi__mode--hidden input[data-mode="car"]').exists()).toBe(false)
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
    const simonSection = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
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
})

describe('SettingsView — destination fields and person-scroll (A6, D2)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('distinguishes the destination name from its address', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const text = wrapper.text()
    expect(text).toContain('Destination name')
    expect(text).toContain('Office / location address')
  })

  it('highlights and scrolls to the person named in the URL', async () => {
    const { wrapper, flush } = await mountView('?person=George')
    await flush()
    const george = wrapper.findAll('.settings-person').find(s => s.text().includes('George'))!
    expect(george.classes()).toContain('settings-person--target')
  })
})

describe('SettingsView — selling-home toggle (P7, B7)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the current-home fields when a home is being sold', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const simon = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
    expect(simon.text()).toContain('I am selling a home to fund this purchase')
    expect(simon.text()).toContain('Expected sale price of current home')
    expect(simon.text()).toContain('Mortgage remaining on current home')
  })

  it('hides the current-home fields for a cash-only person and relabels the deposit', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const ashby = wrapper.findAll('.settings-person').find(s => s.text().includes('Ashby'))!
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
    const simon = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
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
    // Simon has a car -> train+car+walk; Lorena does not -> train+walk
    const simon = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
    await simon.find('button.poi-add').trigger('click')
    await simon.trigger('focusout')
    await flush()
    const [, body] = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0]
    const added = body.places_of_interest[body.places_of_interest.length - 1]
    expect(added.acceptable_modes).toEqual(['train', 'car', 'walk'])
  })

  it('removes a destination row', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const simon = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
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
    const { wrapper, flush } = await mountView()
    await flush()
    const ashby = wrapper.findAll('.settings-person').find(s => s.text().includes('Ashby'))!
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
    const simon = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
    // Pimlico has only ['train'] — unchecking it must not remove the last
    // mode (an empty set would be reinterpreted by the server migration)
    const train = simon.find('input[type="checkbox"][data-mode="train"]')
    await train.setValue(false)
    await simon.trigger('focusout')
    await flush()
    const [name, body] = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(name).toBe('Simon')
    const pimlico = body.places_of_interest.find((p: { label: string }) => p.label === 'Pimlico')
    expect(pimlico.acceptable_modes).toContain('train')
  })
})

describe('SettingsView — autosave status and undo (C2/C3)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows "Saved ✓" with an Undo action after an autosave', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const simon = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
    await simon.find('input#cash').setValue('5000')
    await simon.trigger('focusout')
    await flush()
    expect(wrapper.text()).toContain('Saved ✓')
    expect(simon.find('.settings-person__undo').text()).toContain('Undo')
  })

  it('undo re-patches the previous snapshot', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const simon = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
    await simon.find('input#cash').setValue('5000')
    await simon.trigger('focusout')
    await flush()
    const savedBody = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0][1]
    await simon.find('.settings-person__undo').trigger('click')
    await flush()
    expect(api.patchPerson).toHaveBeenCalledTimes(2)
    expect((api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[1][1]).toEqual(savedBody)
  })

  it('autosaves on the debounce without waiting for blur', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    vi.useFakeTimers()
    const simon = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
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
    const simon = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
    await simon.find('input#cash').setValue('5000')
    await simon.trigger('focusout')
    await flush()
    expect(wrapper.text()).toContain("Couldn't save")
    expect(simon.find('.settings-person__undo').text()).toContain('Retry')
  })
})

describe('SettingsView — empty money inputs normalize on save', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('sends "0" for a cleared money field instead of failing the save', async () => {
    const { wrapper, flush } = await mountView()
    await flush()
    const simon = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
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
    const simon = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
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
    const simon = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
    const sale = simon.find('input#home-sale')
    await sale.setValue('550000')
    const evt = new KeyboardEvent('keydown', { key: '.', cancelable: true })
    sale.element.dispatchEvent(evt)
    expect(evt.defaultPrevented).toBe(true)
    expect((sale.element as HTMLInputElement).value).toBe('550000')
  })
})
