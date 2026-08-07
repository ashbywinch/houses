import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createRouter, createWebHashHistory } from 'vue-router'
import { usePropertiesStore } from '../../stores/properties'
import PropertyDetail from '../PropertyDetail.vue'
import type { PropertyDetail as PropertyDetailType } from '../../types'
import * as api from '../../services/api'

// Mock the API module so fetch-based calls don't fail in test environment
vi.mock('../../services/api', () => ({
  patchTriage: vi.fn().mockResolvedValue({ ok: true }),
  patchAddress: vi.fn(),
  patchLocation: vi.fn(),
  fetchComments: vi.fn().mockResolvedValue([]),
  postComment: vi.fn().mockResolvedValue({ person: 'Ashby', text: '', timestamp: new Date().toISOString() }),
}))

function makeDetail(): PropertyDetailType {
  return {
    rid: '123',
    best_address: { succeeded: true, value: '1 Main St, London', error: null, provenance: { label: 'test' } },
    rightmove_url: { succeeded: true, value: '', error: null, provenance: { label: 'test' } },
    rightmove_price: { succeeded: true, value: {amount: "500000", currency: "GBP"}, error: null, provenance: { label: 'test' } },
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
        value: { label: 'Office', duration: { value: 45, unit: 'minute' }, daily_cost: { amount: "12.5", currency: 'GBP' }, mode: 'transit', _details: [{ legs: [{ mode: 'walk', duration: {value: 5, unit: 'minute'}, end_station: 'Station' }, { mode: 'train', duration: {value: 30, unit: 'minute'}, end_station: 'London Paddington', line_name: 'Great Western' }], cost: null }, { legs: [{ mode: 'tube', duration: {value: 10, unit: 'minute'}, end_station: 'Oxford Circus', line_name: 'Bakerloo' }], cost: null }], is_child: false, route_description: 'Walk to Station → Train 30m → Tube 10m' },
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
      stamp_duty: { succeeded: true, value: {amount: "20000", currency: "GBP"}, error: null, provenance: { label: 'test' } },
      council_tax: { succeeded: false, value: null, error: null, provenance: { label: 'test' } },
      works_estimates: { succeeded: true, value: {}, error: null, provenance: { label: 'test' } },
      total_works: { succeeded: true, value: {amount: "0", currency: "GBP"}, error: null, provenance: { label: 'test' } },
      total_equity: { succeeded: true, value: {amount: "0", currency: "GBP"}, error: null, provenance: { label: 'test' } },
      life_insurance_total: { succeeded: true, value: {amount: "0", currency: "GBP"}, error: null, provenance: { label: 'test' } },
      mortgage_required: { succeeded: true, value: {amount: "0", currency: "GBP"}, error: null, provenance: { label: 'test' } },
      monthly_mortgage: { succeeded: true, value: {amount: "1500", currency: "GBP"}, error: null, provenance: { label: 'test' } },
      monthly_sinking_fund: { succeeded: true, value: {amount: "200", currency: "GBP"}, error: null, provenance: { label: 'test' } },
      monthly_commute_cost: { succeeded: true, value: { persons: { Simon: { daily_gbp: 12.5, yearly_gbp: 5750 } }, yearly_total_gbp: 5750, formula_explanation: 'Aggregated' }, error: null, provenance: { label: 'test' } },
      rental_income: { succeeded: true, value: {amount: "0", currency: "GBP"}, error: null, provenance: { label: 'test' } },
      group_monthly_cost: { succeeded: true, value: { couple: { value: "1700", stddev: 0 }, others: { value: "500", stddev: 0 }, couple_label: "S&L", others_label: "A" }, error: null, provenance: { label: 'test' } },
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

    // Click accordion header to expand commute details
    const header = wrapper.find('.commute-accordion__header')
    await header.trigger('click')
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
      _details: [
        {
          legs: [{ mode: 'train', duration: {value: 30, unit: 'minute'}, end_station: 'London Paddington' }],
          operator: 'GWR',
          cost: 15.5,  // raw number, not {amount, currency}
        },
      ],
    }
    store.loading = false

    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    // Click accordion header to expand
    const header = wrapper.find('.commute-accordion__header')
    await header.trigger('click')
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

describe('PropertyDetail town description', () => {
  it('renders description text not JSON object', async () => {
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
    const detail = makeDetail()
    // Town description comes from the backend as {description: string}
    detail.area.town_description = {
      succeeded: true,
      value: { description: 'Leafy and affluent with village-like charm.' },
      error: null,
      provenance: { label: 'llm' },
    } as any
    store.details['123'] = detail
    store.loading = false

    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const text = wrapper.text()
    // Should show the description text as a readable sentence, not JSON
    expect(text).toContain('Leafy and affluent')
    expect(text).not.toContain('[object Object]')
    expect(text).not.toContain('{"description"')
    expect(text).not.toContain('"description"')
  })
})

describe('PropertyDetail notes', () => {
  it('renders notes section with textarea and save button', async () => {
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

    // Notes section exists
    const section = wrapper.find('#section-notes')
    expect(section.exists()).toBe(true)

    // Textarea for new comments
    const textarea = section.find('textarea')
    expect(textarea.exists()).toBe(true)

    // Save button
    const saveBtn = section.find('button')
    expect(saveBtn.exists()).toBe(true)
    expect(saveBtn.text()).toBe('Save')
  })

  it('calls postComment when save is clicked', async () => {
    const postCommentMock = vi.mocked(api.postComment)
    postCommentMock.mockResolvedValue({
      person: 'Ashby',
      text: 'A new comment',
      timestamp: new Date().toISOString(),
    })

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

    const textarea = wrapper.find('textarea')
    await textarea.setValue('A new comment')

    const saveBtn = wrapper.find('#section-notes .note-input__btn')
    await saveBtn.trigger('click')

    await wrapper.vm.$nextTick()

    expect(postCommentMock).toHaveBeenCalled()
    // The textarea should be cleared after successful post
    expect((textarea.element as HTMLTextAreaElement).value).toBe('')
  })

  it('shows empty state when no comments exist', async () => {
    vi.mocked(api.fetchComments).mockResolvedValue([])

    const router = createRouter({
      history: createWebHashHistory(),
      routes: [{ path: '/property/:rid', component: PropertyDetail }],
    })
    router.push('/property/123')
    await router.isReady()

    const wrapper = mount(PropertyDetail, {
      global: { plugins: [createPinia(), router] },
    })

    const store = usePropertiesStore()
    store.details['123'] = makeDetail()
    store.loading = false
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('#section-notes').text()).toContain('No comments yet.')
  })

  it('renders existing comments with person, text, and timestamp', async () => {
    vi.mocked(api.fetchComments).mockResolvedValue([
      { person: 'Ashby', text: 'First comment', timestamp: '2026-07-01T10:00:00Z' },
      { person: 'Simon', text: 'Second comment', timestamp: '2026-07-02T12:00:00Z' },
    ])

    const router = createRouter({
      history: createWebHashHistory(),
      routes: [{ path: '/property/:rid', component: PropertyDetail }],
    })
    router.push('/property/123')
    await router.isReady()

    const wrapper = mount(PropertyDetail, {
      global: { plugins: [createPinia(), router] },
    })

    const store = usePropertiesStore()
    store.details['123'] = makeDetail()
    store.loading = false
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const notesText = wrapper.find('#section-notes').text()
    expect(notesText).toContain('Ashby')
    expect(notesText).toContain('First comment')
    expect(notesText).toContain('Simon')
    expect(notesText).toContain('Second comment')
  })

  it('calls fetchComments on mount', async () => {
    const fetchCommentsMock = vi.mocked(api.fetchComments)
    fetchCommentsMock.mockResolvedValue([])

    const router = createRouter({
      history: createWebHashHistory(),
      routes: [{ path: '/property/:rid', component: PropertyDetail }],
    })
    router.push('/property/123')
    await router.isReady()

    const wrapper = mount(PropertyDetail, {
      global: { plugins: [createPinia(), router] },
    })

    const store = usePropertiesStore()
    store.details['123'] = makeDetail()
    store.loading = false
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(fetchCommentsMock).toHaveBeenCalledWith('123')
  })

  it('silently handles postComment failure', async () => {
    vi.mocked(api.postComment).mockRejectedValue(new Error('Network error'))

    const router = createRouter({
      history: createWebHashHistory(),
      routes: [{ path: '/property/:rid', component: PropertyDetail }],
    })
    router.push('/property/123')
    await router.isReady()

    const wrapper = mount(PropertyDetail, {
      global: { plugins: [createPinia(), router] },
    })

    const store = usePropertiesStore()
    store.details['123'] = makeDetail()
    store.loading = false
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const textarea = wrapper.find('textarea')
    await textarea.setValue('This will fail')

    const saveBtn = wrapper.find('#section-notes .note-input__btn')
    // Should not throw
    await expect(saveBtn.trigger('click')).resolves.toBeUndefined()
  })

  it('disables save button when textarea is empty', async () => {
    const router = createRouter({
      history: createWebHashHistory(),
      routes: [{ path: '/property/:rid', component: PropertyDetail }],
    })
    router.push('/property/123')
    await router.isReady()

    const wrapper = mount(PropertyDetail, {
      global: { plugins: [createPinia(), router] },
    })

    const store = usePropertiesStore()
    store.details['123'] = makeDetail()
    store.loading = false
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.find('#section-notes .note-input__btn')
    expect((saveBtn.element as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('PropertyDetail map embed', () => {
  it('renders embedded map iframe when location is available', async () => {
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
    const detail = makeDetail()
    store.details['123'] = detail
    store.loading = false

    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const iframe = wrapper.find('iframe')
    expect(iframe.exists()).toBe(true)
    const src = iframe.attributes('src') ?? ''
    expect(src).toContain('openstreetmap.org/export/embed')
    expect(src).toContain('51.5')
    expect(src).toContain('-0.1')
  })

  it('shows placeholder when no location data', async () => {
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
    const detail = makeDetail()
    // Remove location data
    detail.location.best_location = { succeeded: false, value: null, error: 'no data', provenance: { label: 'test' } }
    store.details['123'] = detail
    store.loading = false

    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('iframe').exists()).toBe(false)
    expect(wrapper.text()).toContain('No location data')
  })
})

describe('PropertyDetail address edit (C2)', () => {
  it('lets the user correct the address and refetches so council tax recomputes', async () => {
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
    store.loadDetail = vi.fn().mockResolvedValue(undefined)

    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    await wrapper.find('.summary-address-edit').trigger('click')
    const input = wrapper.find('input.address-edit-input')
    expect(input.exists()).toBe(true)
    await input.setValue('1 Main St, London SW1V 2QQ')
    await wrapper.find('button.address-edit-save').trigger('click')

    expect(api.patchAddress).toHaveBeenCalledWith('123', '1 Main St, London SW1V 2QQ')
    expect(store.loadDetail).toHaveBeenCalledWith('123')
  })
})

describe('PropertyDetail commute settings link (D2)', () => {
  it('links the commute section to settings for the session person', async () => {
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

    const { useAuthStore } = await import('../../stores/auth')
    const auth = useAuthStore()
    auth.user = { email: 'simon@example.com', name: 'Simon', picture: '', person: 'Simon', is_superuser: false } as any

    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const link = wrapper.find('a.change-destinations')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href') ?? '').toContain('#/settings')
    expect(link.attributes('href') ?? '').toContain('person=Simon')
  })
})
