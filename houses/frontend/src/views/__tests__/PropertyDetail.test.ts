import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createRouter, createWebHashHistory } from 'vue-router'
import { usePropertiesStore } from '../../stores/properties'
import PropertyDetail from '../PropertyDetail.vue'
import type { PropertyDetail as PropertyDetailType } from '../../types'

function makeDetail(): PropertyDetailType {
  return {
    rid: '123',
    best_address: { succeeded: true, value: '1 Main St, London', error: null, provenance: { label: 'test' } },
    rightmove_url: { succeeded: true, value: '', error: null, provenance: { label: 'test' } },
    rightmove_price: { succeeded: true, value: '500000', error: null, provenance: { label: 'test' } },
    rightmove_bedrooms: { succeeded: true, value: '3', error: null, provenance: { label: 'test' } },
    postcode: { succeeded: true, value: 'SW1V 2QQ', error: null, provenance: { label: 'test' } },
    location: {
      best_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
      geocode: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
      rightmove_location: { succeeded: true, value: { lat: 51.5, lon: -0.1 }, error: null, provenance: { label: 'test' } },
      precise_location: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    },
    commutes: {
      'Simon/Office': {
        succeeded: true,
        value: { label: 'Office', duration: { value: 45, unit: 'minute' }, daily_cost: { amount: 12.5, currency: 'GBP' }, mode: 'transit', details: [{ legs: [{ mode: 'walk', duration_minutes: 5, end_station: 'Station' }, { mode: 'train', duration_minutes: 30, end_station: 'London Paddington', line_name: 'Great Western' }], cost: null }, { legs: [{ mode: 'tube', duration_minutes: 10, end_station: 'Oxford Circus', line_name: 'Bakerloo' }], cost: null }], is_child: false, route_description: 'Walk to Station → Train 30m → Tube 10m' },
        is_child: false,
        error: null,
        provenance: { label: 'commute' },
      } as any,
    },
    schools: {
      primary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
      secondary: { school: { succeeded: false, value: null, error: null, provenance: { label: 'test' } } },
    },
    affordability: {
      stamp_duty: { succeeded: true, value: 20000, error: null, provenance: { label: 'test' } },
      council_tax: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      monthly_mortgage: { succeeded: true, value: 1500, error: null, provenance: { label: 'test' } },
      monthly_sinking_fund: { succeeded: true, value: 200, error: null, provenance: { label: 'test' } },
      monthly_commute_cost: { succeeded: true, value: { persons: { Simon: { daily_gbp: 12.5, yearly_gbp: 5750 } }, yearly_total_gbp: 5750, formula_explanation: 'Aggregated' }, error: null, provenance: { label: 'test' } },
      total_monthly_housing_cost: { succeeded: true, value: 1700, error: null, provenance: { label: 'test' } },
    },
    area: {
      walkability: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      town_description: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    },
    comments: {
      status: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      status_reason: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      group_notes: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      ashby_comments: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      ashby_works_estimate: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      design_needed: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      planning_needed: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
    },
    settings: {
      persons: { succeeded: true, value: [], error: null, provenance: { label: 'test' } },
      financial: { succeeded: true, value: {}, error: null, provenance: { label: 'test' } },
    },
  }
}

describe('PropertyDetail renders commute legs from CostGroups', () => {
  it('renders leg details with mode and duration from nested CostGroups', async () => {
    const router = createRouter({
      history: createWebHashHistory(),
      routes: [{ path: '/property/:rid', component: PropertyDetail }],
    })
    router.push('/property/123')
    await router.isReady()

    const wrapper = mount(PropertyDetail, {
      global: {
        plugins: [createPinia(), router],
      },
    })

    const store = usePropertiesStore()
    store.details['123'] = makeDetail()
    store.loading = false

    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const text = wrapper.text()
    expect(text).toContain('walk')
    expect(text).toContain('train')
    expect(text).toContain('tube')
    expect(text).toContain('5 min')
    expect(text).toContain('30 min')
    expect(text).toContain('10 min')
    expect(text).toContain('London Paddington')
  })

  it('displays cost correctly for both raw number and {amount,currency} formats', async () => {
    const router = createRouter({
      history: createWebHashHistory(),
      routes: [{ path: '/property/:rid', component: PropertyDetail }],
    })
    router.push('/property/123')
    await router.isReady()

    const wrapper = mount(PropertyDetail, {
      global: {
        plugins: [createPinia(), router],
      },
    })

    const store = usePropertiesStore()
    store.details['123'] = makeDetail()
    // Override the commute to include a CostGroup with raw-number cost
    const commute = store.details['123'].commutes['Simon/Office']
    commute.value = {
      ...commute.value,
      details: [
        {
          legs: [{ mode: 'train', duration_minutes: 30, end_station: 'London Paddington' }],
          operator: 'GWR',
          cost: 15.5,  // raw number, not {amount, currency}
        },
      ],
    }
    store.loading = false

    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const text = wrapper.text()
    expect(text).toContain('train')
    expect(text).toContain('30 min')
    // Should NOT display NaN — cost should render correctly
    expect(text).not.toContain('NaN')
    // Should show the raw-number cost
    expect(text).toContain('15.50')
  })
})
