import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
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
  }
}

function mountView() {
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.user = { email: 'simon@example.com', name: 'Simon', picture: '', person: 'Simon', is_superuser: true } as any
  ;(api.fetchSettings as ReturnType<typeof vi.fn>).mockResolvedValue(makeSettings())
  return { wrapper: mount(SettingsView), flush: flushPromises }
}

describe('SettingsView — family sections', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders a section per person', async () => {
    const { wrapper, flush } = mountView()
    await flush()
    const text = wrapper.text()
    expect(text).toContain('Simon')
    expect(text).toContain('Lorena')
    expect(text).toContain('George')
  })

  it('marks the session person with a "you" badge and the child with a child badge', async () => {
    const { wrapper, flush } = mountView()
    await flush()
    expect(wrapper.text()).toContain('you')
    expect(wrapper.text()).toContain('child')
  })

  it('renders the school note for a child with no-address POIs', async () => {
    const { wrapper, flush } = mountView()
    await flush()
    expect(wrapper.text()).toContain('Goes to school near the house')
  })
})

describe('SettingsView — ownership rendering', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('makes the own person editable with a save button', async () => {
    const { wrapper, flush } = mountView()
    await flush()
    const simonSection = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
    expect(simonSection.find('button.save').exists()).toBe(true)
    expect(simonSection.find('input[type="checkbox"][data-mode="walk"]').attributes('disabled')).toBeUndefined()
  })

  it('locks other people — read-only, no save button', async () => {
    const { wrapper, flush } = mountView()
    await flush()
    const lorenaSection = wrapper.findAll('.settings-person').find(s => s.text().includes('Lorena'))!
    expect(lorenaSection.text()).toContain('read-only')
    expect(lorenaSection.find('button.save').exists()).toBe(false)
  })

  it('does not offer the car mode to people without a car', async () => {
    const { wrapper, flush } = mountView()
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

describe('SettingsView — saving', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('saves the edited person and their thresholds via PATCH', async () => {
    const { wrapper, flush } = mountView()
    await flush()
    const simonSection = wrapper.findAll('.settings-person').find(s => s.text().includes('Simon'))!
    // tick a mode checkbox, then save
    const walk = simonSection.find('input[type="checkbox"][data-mode="walk"]')
    await walk.setValue(true)
    await simonSection.find('button.save').trigger('click')
    expect(api.patchPerson).toHaveBeenCalledTimes(1)
    const [name, body] = (api.patchPerson as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(name).toBe('Simon')
    const pimlico = body.places_of_interest.find((p: { label: string }) => p.label === 'Pimlico')
    expect(pimlico.acceptable_modes).toContain('walk')
    expect(body.thresholds).toEqual({ good_max_minutes: 30, fine_max_minutes: 45 })
  })
})
