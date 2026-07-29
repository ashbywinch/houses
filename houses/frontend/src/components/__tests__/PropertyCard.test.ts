import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
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
    total_monthly_cost: { succeeded: true, value: {amount: "2500", currency: "GBP"}, error: null, provenance: { label: 'test' } },
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
    expect(wrapper.text()).toContain('20m')
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

  it('shows total monthly cost', () => {
    const wrapper = mountCard({ rid: '123', data: makeSummary() })
    expect(wrapper.text()).toContain('£2,500')
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

describe('PropertyCard error handling', () => {
  it('handles missing price', () => {
    const summary = makeSummary({
      rightmove_price: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      total_monthly_cost: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    })
    const wrapper = mountCard({ rid: '123', data: summary })
    expect(wrapper.text()).not.toContain('£500,000')
    expect(wrapper.text()).not.toContain('/mo')
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

