import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PropertyCard from '../PropertyCard.vue'
import { usePropertiesStore } from '../../stores/properties'
import type { PropertySummary } from '../../types'

function mountCard(props: { rid: string; data: PropertySummary }) {
  const pinia = createPinia()
  const wrapper = mount(PropertyCard, { props, global: { plugins: [pinia] } })
  // Ensure Pinia store is available
  const store = usePropertiesStore()
  store.triage[props.rid] = { favourite: false, dismissed: false, is_viewed: false, user_notes: '', triage_status: '' }
  return wrapper
}

function makeSummary(overrides?: Partial<PropertySummary>): PropertySummary {
  return {
    rid: '123',
    best_address: { succeeded: true, value: '1 Main St, London', error: null, provenance: { label: 'test' } },
    best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
    rightmove_price: { succeeded: true, value: {amount: "500000", currency: 'GBP'}, error: null, provenance: { label: 'test' } },
    rightmove_bedrooms: { succeeded: true, value: '3', error: null, provenance: { label: 'test' } },
    total_monthly_cost: { succeeded: true, value: { value: { amount: "2500", currency: "GBP" }, stddev: 0 }, error: null, provenance: { label: 'test' } },
    group_monthly_cost: {
      succeeded: true,
      value: { couple: { value: '2100.00', stddev: 0 }, others: { value: '400.00', stddev: 0 }, couple_label: 'Simon & Lorena', others_label: 'Ashby' },
      error: null, provenance: { label: 'test' },
    },
    walkability: { succeeded: true, value: { walk_to_town: {value: 15, unit: 'minute'} }, error: null, provenance: { label: 'test' } },
    town_name: { succeeded: true, value: 'London', error: null, provenance: { label: 'test' } },
    commutes: {},
    schools: {
      primary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
      secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
    },
    ...overrides,
  }
}

describe('PropertyCard commute filtering', () => {
  it('renders adult commutes', () => {
    const summary = makeSummary({
      commutes: {
        'Simon/Pimlico': {
          commute: {
            succeeded: true, value: { duration: { value: 32, unit: 'minute' }, label: 'Pimlico' },
            error: null, provenance: { label: 'test' },
            is_child: false,
          },
        },
      },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    expect(wrapper.text()).toContain('Pimlico')
  })

  it('hides child commutes from commute section', () => {
    const summary = makeSummary({
      commutes: {
        'George/Primary School': {
          commute: {
            succeeded: true, value: { duration: { value: 20, unit: 'minute' }, label: 'Primary School' },
            error: null, provenance: { label: 'test' },
            is_child: true,
          },
        },
      },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    expect(wrapper.text()).not.toContain('Primary School')
  })

  it('mixes adult and child commutes correctly', () => {
    const summary = makeSummary({
      commutes: {
        'Simon/Pimlico': {
          commute: {
            succeeded: true, value: { duration: { value: 32, unit: 'minute' }, label: 'Pimlico' },
            error: null, provenance: { label: 'test' },
            is_child: false,
          },
        },
        'George/Primary School': {
          commute: {
            succeeded: true, value: { duration: { value: 20, unit: 'minute' }, label: 'Primary School' },
            error: null, provenance: { label: 'test' },
            is_child: true,
          },
        },
      },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    expect(wrapper.text()).toContain('Pimlico')
    expect(wrapper.text()).not.toContain('Primary School')
  })

  it('shows school commutes in schools section', () => {
    const summary = makeSummary({
      commutes: {
        'George/Primary School': {
          commute: {
            succeeded: true, value: { duration: { value: 20, unit: 'minute' }, label: 'Primary School', is_child: true },
            error: null, provenance: { label: 'test' },
            is_child: true,
          },
        },
      },
      schools: {
        primary: {
          school: {
            succeeded: true,
            value: { name: 'Test Primary', ofsted: 'Good', distance: {value: 1, unit: 'km'}, url: '' },
            error: null, provenance: { label: 'test' },
          },
        },
        secondary: {
          school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
        },
      },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    expect(wrapper.text()).toContain('Test Primary')
    // School commute time should appear alongside the school name
    expect(wrapper.text()).toContain('20 min walk')
  })
})

describe('PropertyCard basic rendering', () => {
  it('renders price and bedrooms', () => {
    const wrapper = mountCard({ rid: '123', data: makeSummary() })
    expect(wrapper.text()).toContain('£500,000')
    expect(wrapper.text()).toContain('3 bed')
  })

  it('renders town name', () => {
    const wrapper = mountCard({ rid: '123', data: makeSummary() })
    expect(wrapper.text()).toContain('London')
  })

  it('falls back to rid when address fails', () => {
    const summary = makeSummary({
      best_address: { succeeded: false, value: null, error: 'fail', provenance: { label: 'test' } },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    expect(wrapper.text()).toContain('123')
  })

  it('shows the two headline numbers, labelled by the groups', () => {
    const wrapper = mountCard({ rid: '123', data: makeSummary() })
    expect(wrapper.text()).toContain('Simon & Lorena £2,100/mo')
    expect(wrapper.text()).toContain('Ashby £400/mo')
  })

  it('renders ≈ prefix with the one-step reason when total is approximate (Part A)', () => {
    const summary = makeSummary({
      total_monthly_cost: {
        succeeded: true,
        value: { value: { amount: '2500', currency: 'GBP' }, stddev: 50 },
        error: null,
        provenance: { label: 'test' },
      },
      group_monthly_cost: {
        succeeded: true,
        value: { couple: { value: '2100', stddev: 50 }, others: { value: '400', stddev: 0 }, couple_label: 'Simon & Lorena', others_label: 'Ashby' },
        error: null, provenance: { label: 'test' },
      },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    expect(wrapper.text()).toContain('≈£2,100/mo')
    expect(wrapper.find('.card__monthly-cost .card__cost-line').attributes('title')).toContain('approximate')
  })

  it('marks a commute whose office was renamed or removed as old (C4)', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = usePropertiesStore()
    store.poiLabels = { Simon: ['Pimlico'] }
    const summary = makeSummary({
      commutes: {
        'Simon/Old Office': {
          commute: {
            succeeded: true, value: { duration: { value: 32, unit: 'minute' }, label: 'Old Office' },
            error: null, provenance: { label: 'test' },
          },
        },
      },
    })
    const wrapper = mount(PropertyCard, { props: { rid: '123', data: summary }, global: { plugins: [pinia] } })
    expect(wrapper.text()).toContain('old office')
  })

  it('shows freshness badge with property_added_at', () => {
    const summary = makeSummary({
      freshness: { property_added_at: new Date().toISOString() },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    expect(wrapper.text()).toContain('Added today')
  })

  it('shows Added Xd ago for old properties', () => {
    const oldDate = new Date()
    oldDate.setDate(oldDate.getDate() - 5)
    const summary = makeSummary({
      freshness: { property_added_at: oldDate.toISOString() },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    expect(wrapper.text()).toContain('Added 5d ago')
  })
})

describe('PropertyCard commute attribution (P2)', () => {
  it('labels commute rows with the person and the destination', () => {
    const summary = makeSummary({
      commutes: {
        'Simon/Pimlico': {
          commute: {
            succeeded: true,
            value: { duration: { value: 32, unit: 'minute' }, label: 'Pimlico', person: { name: 'Simon' } },
            error: null, provenance: { label: 'test' }, is_child: false,
          },
        },
      },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    expect(wrapper.text()).toContain('Simon → Pimlico')
  })

  it('falls back to the commute key for the person name', () => {
    const summary = makeSummary({
      commutes: {
        'Lorena/Aldgate': {
          commute: {
            succeeded: true,
            value: { duration: { value: 40, unit: 'minute' }, label: 'Aldgate' },
            error: null, provenance: { label: 'test' }, is_child: false,
          },
        },
      },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    expect(wrapper.text()).toContain('Lorena → Aldgate')
  })

  it('points directions at the destination address, not the label', () => {
    const summary = makeSummary({
      commutes: {
        'Simon/Pimlico': {
          commute: {
            succeeded: true,
            value: {
              duration: { value: 32, unit: 'minute' },
              label: 'Pimlico',
              person: { name: 'Simon' },
              destination: { label: 'Pimlico', address: '1 Drummond Gate, Pimlico, London SW1V 2QQ' },
            },
            error: null, provenance: { label: 'test' }, is_child: false,
          },
        },
      },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    const href = wrapper.find('a.pill-link').attributes('href') ?? ''
    expect(href).toContain(encodeURIComponent('1 Drummond Gate, Pimlico, London SW1V 2QQ'))
  })
})

describe('PropertyCard affordability honesty (P2)', () => {
  it('shows a muted can-not-calculate marker when the total is impossible', () => {
    const summary = makeSummary({
      total_monthly_cost: { succeeded: false, value: null, error: 'x', provenance: { label: 'test' } },
      group_monthly_cost: { succeeded: false, value: null, error: 'x', provenance: { label: 'test' } },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    expect(wrapper.text()).toContain('£—/mo')
  })

  it('shows BOTH per-month rows when the group total is impossible', async () => {
    // Regression: an impossible monthly payment collapsed the card to a
    // single unlabelled £—/mo. The two groups (joint owners + other
    // adults) must still render as separate labelled rows, each showing
    // the unknown marker — the labels come from the settings persons.
    const summary = makeSummary({
      total_monthly_cost: { succeeded: false, value: null, error: 'x', provenance: { label: 'test' } },
      group_monthly_cost: { succeeded: false, value: null, error: 'Works estimate required for: Ashby', provenance: { label: 'test' } },
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(PropertyCard, { props: { rid: '123', data: summary }, global: { plugins: [pinia] } })
    const store = usePropertiesStore()
    store.triage['123'] = { favourite: false, dismissed: false, is_viewed: false, user_notes: '', triage_status: '' }
    // settings persons: Simon+Lorena own the home, Ashby is the other adult
    store.groupLabels = { coupleLabel: 'S+L', othersLabel: 'Ashby' }
    await wrapper.vm.$nextTick()
    const lines = wrapper.findAll('.card__cost-line')
    expect(lines.length).toBe(2)
    expect(lines[0].text()).toContain('S+L')
    expect(lines[0].text()).toContain('£—/mo')
    expect(lines[1].text()).toContain('Ashby')
    expect(lines[1].text()).toContain('£—/mo')
  })

  it('explains the TfL daily maximum instead of presenting it as a fare', () => {
    const summary = makeSummary({
      commutes: {
        'Simon/Pimlico': {
          commute: {
            succeeded: true,
            value: {
              duration: { value: 87, unit: 'minute' },
              label: 'Pimlico',
              daily_cost: { amount: '100.00', currency: 'GBP' },
            },
            error: null, provenance: { label: 'test' }, is_child: false,
          },
        },
      },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    const pill = wrapper.find('.card__commute-data .pill')
    expect(pill.text()).toContain('(max)')
    expect(pill.attributes('title') ?? '').toContain('TfL daily maximum')
  })
})

describe('PropertyCard error handling', () => {
  it('handles missing price', () => {
    const summary = makeSummary({
      rightmove_price: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      total_monthly_cost: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      group_monthly_cost: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    expect(wrapper.text()).not.toContain('£500,000')
    // an uncomputable total is never hidden silently — it reads as unknown
    expect(wrapper.text()).toContain('£—/mo')
  })

  it('handles empty commutes', () => {
    const wrapper = mountCard({ rid: '123', data: makeSummary() })
    expect(wrapper.find('.card__commutes').exists()).toBe(true)
  })

  it('handles empty ofsted string without throwing', () => {
    const summary = makeSummary({
      schools: {
        primary: { school: { succeeded: true, value: { name: 'Test Primary', ofsted: '', distance: {value: 1, unit: 'km'}, url: '' }, error: null, provenance: { label: 'test' } } },
        secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
      },
    })
    expect(() => mountCard({ rid: '123', data: summary })).not.toThrow()
  })

  it('handles null ofsted value without throwing', () => {
    const summary = makeSummary({
      schools: {
        primary: { school: { succeeded: true, value: { name: 'Test Primary', ofsted: null as unknown as string, distance: {value: 1, unit: 'km'}, url: '' }, error: null, provenance: { label: 'test' } } },
        secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
      },
    })
    expect(() => mountCard({ rid: '123', data: summary })).not.toThrow()
  })

  it('handles missing ofsted property without throwing', () => {
    const summary = makeSummary({
      schools: {
        primary: { school: { succeeded: true, value: { name: 'Test Primary', distance: {value: 1, unit: 'km'}, url: '' } as unknown as { name: string; ofsted: string; distance: {value: number, unit: string}; url: string }, error: null, provenance: { label: 'test' } } },
        secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
      },
    })
    expect(() => mountCard({ rid: '123', data: summary })).not.toThrow()
  })

})

describe('PropertyCard triage markers', () => {
  const noTriage = { favourite: false, dismissed: false, is_viewed: false, user_notes: '', triage_status: '' }

  function mountWithTriage(triage: typeof noTriage) {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = usePropertiesStore()
    store.triage['123'] = triage
    const wrapper = mount(PropertyCard, { props: { rid: '123', data: makeSummary() }, global: { plugins: [pinia] } })
    return { wrapper, store }
  }

  it('has NO colour bars at all (status bar and accent border are gone)', () => {
    const { wrapper } = mountWithTriage(noTriage)
    expect(wrapper.find('.card__status').exists()).toBe(false)
    expect(wrapper.find('.card__border').exists()).toBe(false)
  })

  it('shows the favourite heart icon on favourited cards only', async () => {
    const { wrapper, store } = mountWithTriage(noTriage)
    expect(wrapper.find('.card__fav-icon').exists()).toBe(false)
    store.triage['123'] = { ...noTriage, favourite: true }
    await wrapper.vm.$nextTick()
    const icon = wrapper.find('.card__fav-icon')
    expect(icon.exists()).toBe(true)
    expect(icon.attributes('aria-label')).toBe('Favourite')
  })

  it('shows a Seen tag on viewed cards only', async () => {
    const { wrapper, store } = mountWithTriage(noTriage)
    expect(wrapper.find('.card__tag--seen').exists()).toBe(false)
    store.triage['123'] = { ...noTriage, is_viewed: true }
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.card__tag--seen').text()).toBe('Seen')
  })

  it('greys the whole card when dismissed', async () => {
    const { wrapper, store } = mountWithTriage(noTriage)
    expect(wrapper.find('.card').classes()).not.toContain('card--dismissed')
    store.triage['123'] = { ...noTriage, dismissed: true }
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.card').classes()).toContain('card--dismissed')
  })
})



describe('PropertyCard commute colour bands', () => {
  function commuteSummary(mins: number): PropertySummary {
    return makeSummary({
      commutes: {
        'Lorena/Office': {
          commute: {
            succeeded: true,
            value: { duration: { value: mins, unit: 'minute' }, label: 'Office' },
            error: null, provenance: { label: 'test' }, is_child: false,
          },
        },
      },
    })
  }

  it('uses the person\'s own thresholds for the pill colours, not a global constant', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = usePropertiesStore()
    // Lorena's bands: good 40, fine 60 (from Settings)
    store.commuteGoods = { Lorena: 40 }
    store.commuteCeilings = { Lorena: { fine: 60, isChild: false } }
    // 50 min: over good (40) but under fine (60) → 'getting tight'
    const tight = mount(PropertyCard, { props: { rid: '123', data: commuteSummary(50) }, global: { plugins: [pinia] } })
    expect(tight.find('.card__commute-data .pill').classes()).toContain('pill--warn')
    // 70 min: over fine (60) → 'yikes' — the old hardcoded 75 would say 'tight'
    const yikes = mount(PropertyCard, { props: { rid: '123', data: commuteSummary(70) }, global: { plugins: [pinia] } })
    expect(yikes.find('.card__commute-data .pill').classes()).toContain('pill--bad')
  })
})
