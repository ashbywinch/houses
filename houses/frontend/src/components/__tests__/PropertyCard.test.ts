import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import PropertyCard from '../PropertyCard.vue'
import type { PropertySummary } from '../../types'

function makeSummary(overrides?: Partial<PropertySummary>): PropertySummary {
  return {
    rid: '123',
    best_address: { succeeded: true, value: '1 Main St, London', error: null, provenance: { label: 'test' } },
    best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
    rightmove_price: { succeeded: true, value: '500000', error: null, provenance: { label: 'test' } },
    rightmove_bedrooms: { succeeded: true, value: '3', error: null, provenance: { label: 'test' } },
    total_monthly_cost: { succeeded: true, value: 2500, error: null, provenance: { label: 'test' } },
    walkability: { succeeded: true, value: { walk_to_town_minutes: 15 }, error: null, provenance: { label: 'test' } },
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
    const wrapper = mount(PropertyCard, { props: { rid: '123', data: summary } })
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
    const wrapper = mount(PropertyCard, { props: { rid: '123', data: summary } })
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
    const wrapper = mount(PropertyCard, { props: { rid: '123', data: summary } })
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
            value: { name: 'Test Primary', ofsted: 'Good', distance_km: 1, url: '' },
            error: null, provenance: { label: 'test' },
          },
        },
        secondary: {
          school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
        },
      },
    })
    const wrapper = mount(PropertyCard, { props: { rid: '123', data: summary } })
    expect(wrapper.text()).toContain('Test Primary')
  })
})

describe('PropertyCard basic rendering', () => {
  it('renders price and bedrooms', () => {
    const wrapper = mount(PropertyCard, { props: { rid: '123', data: makeSummary() } })
    expect(wrapper.text()).toContain('£500,000')
    expect(wrapper.text()).toContain('3 bed')
  })

  it('renders walk time', () => {
    const wrapper = mount(PropertyCard, { props: { rid: '123', data: makeSummary() } })
    expect(wrapper.text()).toContain('15')
  })

  it('renders town name', () => {
    const wrapper = mount(PropertyCard, { props: { rid: '123', data: makeSummary() } })
    expect(wrapper.text()).toContain('London')
  })

  it('falls back to rid when address fails', () => {
    const summary = makeSummary({
      best_address: { succeeded: false, value: null, error: 'fail', provenance: { label: 'test' } },
    })
    const wrapper = mount(PropertyCard, { props: { rid: '123', data: summary } })
    expect(wrapper.text()).toContain('123')
  })

  it('shows total monthly cost', () => {
    const wrapper = mount(PropertyCard, { props: { rid: '123', data: makeSummary() } })
    expect(wrapper.text()).toContain('£2,500')
  })
})

describe('PropertyCard error handling', () => {
  it('handles missing price', () => {
    const summary = makeSummary({
      rightmove_price: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      total_monthly_cost: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    })
    const wrapper = mount(PropertyCard, { props: { rid: '123', data: summary } })
    // No price on the card header (the £500k section)
    expect(wrapper.text()).not.toContain('£500,000')
    // Total monthly shows 'unknown' when data is missing
    expect(wrapper.text()).toContain('unknown')
  })


  it('handles empty commutes', () => {
    const wrapper = mount(PropertyCard, { props: { rid: '123', data: makeSummary() } })
    expect(wrapper.find('.card__commutes').exists()).toBe(true)
  })
})
