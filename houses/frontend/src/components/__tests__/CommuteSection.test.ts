import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import CommuteSection from '../CommuteSection.vue'

// The on-demand contract: the wire carries no provenance — clicking a ⓘ
// loads the property's provenance map once via the real store + a mocked
// fetch; the maps below serve the fixture trees keyed by endpoint path.
const provApi = vi.hoisted(() => ({ maps: {} as Record<string, Record<string, unknown>> }))
vi.mock('../../services/api', () => ({
  fetchPropertyProvenance: vi.fn(async (rid: string) => provApi.maps[rid] ?? {}),
}))


function mountSection(commutes: unknown, rid: string | undefined = 'r1') {
  setActivePinia(createPinia())
  return mount(CommuteSection, { props: { commutes, rid } })
}

function provMapFrom(commutes: Record<string, { provenance: unknown }>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(commutes).map(([key, entry]) => [`commutes.${key}`, entry.provenance]))
}

beforeEach(() => {
  vi.clearAllMocks()
  provApi.maps = {}
})

function makeCommutes(mode: string, rid: string = 'r1') {
  const commutes = {
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
  provApi.maps[rid] = provMapFrom(commutes)
  return commutes
}

async function openProvenance(wrapper: VueWrapper) {
  await wrapper.find('.commute-accordion button').trigger('click') // expand the accordion
  await wrapper.find('.provenance-toggle__trigger').trigger('click') // show provenance
  await flushPromises() // the on-demand load resolves before assertions
}

describe('CommuteSection provenance (round-2 walkthrough)', () => {
  it('hides petrol sources for a transit route', async () => {
    const wrapper = mountSection(makeCommutes('transit', 'r1'), 'r1')
    await openProvenance(wrapper)
    const text = wrapper.text()
    expect(text).not.toContain('Petrol MPG')
    expect(text).not.toContain('Petrol Cost per Litre')
    expect(text).toContain('Merge')
  })

  it('keeps petrol sources for a drive route', async () => {
    const wrapper = mountSection(makeCommutes('drive', 'r2'), 'r2')
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
