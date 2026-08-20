import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import CommuteSection from '../CommuteSection.vue'

function mountSection(commutes: unknown) {
  setActivePinia(createPinia())
  return mount(CommuteSection, { props: { commutes } })
}

function makeCommutes(mode: string) {
  return {
    'Simon/Pimlico': {
      succeeded: true,
      value: {
        mode,
        duration: { value: 32, unit: 'minute' },
        daily_cost: { amount: '48.27', currency: 'GBP' },
        details: [],
      },
      error: null,
      provenance: {
        label: 'Simon/Pimlico commute',
        sourceType: 'calc',
        sources: {
          petrol_mpg: { label: 'Petrol MPG', value: '45', sourceType: 'user' },
          petrol_cost: { label: 'Petrol Cost per Litre', value: '1.45', sourceType: 'user' },
          merge: { label: 'Merge', value: 'ok', sourceType: 'calc' },
        },
      },
    },
  }
}

async function openProvenance(wrapper: VueWrapper) {
  await wrapper.find('.commute-accordion button').trigger('click') // expand the accordion
  await wrapper.find('.how-btn').trigger('click') // show provenance
}

describe('CommuteSection provenance (round-2 walkthrough)', () => {
  it('hides petrol sources for a transit route', async () => {
    const wrapper = mountSection(makeCommutes('transit'))
    await openProvenance(wrapper)
    const text = wrapper.text()
    expect(text).not.toContain('Petrol MPG')
    expect(text).not.toContain('Petrol Cost per Litre')
    expect(text).toContain('Merge')
  })

  it('keeps petrol sources for a drive route', async () => {
    const wrapper = mountSection(makeCommutes('drive'))
    await openProvenance(wrapper)
    expect(wrapper.text()).toContain('Petrol MPG')
  })
})

describe('CommuteSection no-route reason', () => {
  it('shows the TfL no-route reason instead of the generic message', async () => {
    const commutes = {
      'Simon/Bracknell': {
        succeeded: true,
        value: {
          mode: 'transit',
          duration: null,
          daily_cost: { amount: '0', currency: 'GBP' },
          details: [],
          infeasible: true,
          no_route_reason: "TfL couldn't find a route for this journey (HTTP 404, bus mode excluded)",
        },
        error: null,
        provenance: { label: 'Simon/Bracknell commute', sourceType: 'calc', sources: {} },
      },
    }
    const wrapper = mountSection(commutes)
    await wrapper.find('.commute-accordion button').trigger('click') // expand
    expect(wrapper.text()).toContain('HTTP 404')
    expect(wrapper.text()).not.toContain('check the address in Settings')
  })
})
