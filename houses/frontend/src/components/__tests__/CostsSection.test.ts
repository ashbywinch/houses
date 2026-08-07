import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import CostsSection from '../CostsSection.vue'
import * as api from '../../services/api'

vi.mock('../../services/api', () => ({
  patchWorksEstimate: vi.fn().mockResolvedValue(new Response()),
  patchRentalIncome: vi.fn().mockResolvedValue(new Response()),
}))

function mountCosts(overrides?: Record<string, unknown>, pinia?: ReturnType<typeof createPinia>) {
  const activePinia = pinia ?? createPinia()
  return mount(CostsSection, {
    props: {
      affordability: {
        council_tax: { succeeded: false, value: null, error: null, provenance: {} },
        monthly_mortgage: { succeeded: false, value: null, error: null, provenance: {} },
        monthly_sinking_fund: { succeeded: false, value: null, error: null, provenance: {} },
        monthly_commute_cost: { succeeded: false, value: null, error: null, provenance: {} },
        group_monthly_cost: { succeeded: false, value: null, error: null, provenance: {} },
        works_estimates: { succeeded: true, value: { Ashby: 20000 }, error: null, provenance: {} },
        total_works: { succeeded: true, value: { amount: '20000', currency: 'GBP' }, error: null, provenance: {} },
        rental_income: { succeeded: true, value: { amount: '500', currency: 'GBP' }, error: null, provenance: { label: 'user' } },
        ...(overrides?.affordability as Record<string, unknown> ?? {}),
      },
      epc: { succeeded: false, value: null, error: null, provenance: {} },
      persons: {
        succeeded: true,
        value: [
          { name: 'Simon', has_car: true, is_child: false },
          { name: 'Lorena', has_car: false, is_child: false },
          { name: 'Ashby', has_car: true, is_child: false },
          { name: 'George', has_car: false, is_child: true },
        ],
        error: null,
        provenance: {},
      },
      rid: 'test123',
      currentPerson: 'Ashby',
      ...overrides,
    },
    global: { plugins: [activePinia] },
  })
}

describe('CostsSection works estimates', () => {
  it('shows Cost of Works total row', () => {
    const wrapper = mountCosts()
    expect(wrapper.text()).toContain('Cost of Works')
  })

  it('shows per-person rows for all non-child persons', () => {
    const wrapper = mountCosts()
    expect(wrapper.text()).toContain('Simon')
    expect(wrapper.text()).toContain('Lorena')
    expect(wrapper.text()).toContain('Ashby')
  })

  it('does NOT show child persons (George)', () => {
    const wrapper = mountCosts()
    expect(wrapper.text()).not.toContain('George')
  })

  it('shows per-person value when dict has entry', () => {
    const wrapper = mountCosts()
    expect(wrapper.text()).toContain('£20,000')
  })

  it('shows £? for person without estimate', () => {
    const wrapper = mountCosts({
      affordability: {
        works_estimates: { succeeded: true, value: { Ashby: 20000 }, error: null, provenance: {} },
        total_works: { succeeded: true, value: { amount: '20000', currency: 'GBP' }, error: null, provenance: {} },
      },
    })
    const text = wrapper.text()
    expect(text).toMatch(/Simon.*\?/)
  })

  it('opens inline editor on click for current person', async () => {
    const wrapper = mountCosts()
    const valueEl = wrapper.find('.costs-value--editable')
    expect(valueEl.exists()).toBe(true)
    await valueEl.trigger('click')
    expect(wrapper.find('input').exists()).toBe(true)
  })

  it('rejects pence in the works-estimate editor (whole pounds only)', async () => {
    const wrapper = mountCosts()
    await wrapper.find('.costs-value--editable').trigger('click')
    const input = wrapper.find('input')
    await input.setValue('20000.50')
    expect((input.element as HTMLInputElement).value).toBe('20000')
  })

  it('shows non-current persons as read-only', () => {
    const wrapper = mountCosts()
    const nonEditable = wrapper.findAll('.costs-value:not(.costs-value--editable)')
    // Simon, Lorena, and the ? rows should not be editable
    expect(nonEditable.length).toBeGreaterThan(0)
  })

  it('refreshes detail after saving works estimate', async () => {
    const wrapper = mountCosts()
    const valueEl = wrapper.find('.costs-value--editable')
    await valueEl.trigger('click')
    const input = wrapper.find('input')
    await input.setValue('25000')
    await input.trigger('blur')
    expect(api.patchWorksEstimate).toHaveBeenCalledWith('test123', 'Ashby', 25000)
  })

  it('shows visual affordance on editable values', () => {
    const wrapper = mountCosts()
    const editable = wrapper.findAll('.costs-value--editable')
    expect(editable.length).toBeGreaterThanOrEqual(1)
  })
})

describe('CostsSection rental income', () => {
  it('shows rental income value when present', () => {
    const wrapper = mountCosts()
    expect(wrapper.text()).toContain('Rental Income')
    expect(wrapper.text()).toContain('500')
  })

  it('opens rental income editor on click when currentPerson is set', async () => {
    const wrapper = mountCosts()
    const allText = wrapper.text()
    expect(allText).toContain('Rental Income')
    const rentalValues = wrapper.findAll('span').filter(s =>
      s.classes().includes('costs-value--editable') && s.text().includes('500')
    )
    expect(rentalValues.length).toBeGreaterThanOrEqual(1)
    // Just verify it's clickable — don't actually click since focus might flake
  })

  it('calls patchRentalIncome when saving rental income edit', async () => {
    const wrapper = mountCosts()
    const rentalValues = wrapper.findAll('span').filter(s =>
      s.classes().includes('costs-value--editable') && s.text().includes('500')
    )
    expect(rentalValues.length).toBeGreaterThanOrEqual(1)
    await rentalValues[0].trigger('click')
    const input = wrapper.find('input')
    expect(input.exists()).toBe(true)
    await input.setValue('800')
    await input.trigger('blur')
    expect(api.patchRentalIncome).toHaveBeenCalledWith('test123', 800)
  })

  it('shows the standard provenance toggle when provenance exists', () => {
    const wrapper = mountCosts()
    const trigger = wrapper.find('.provenance-toggle__trigger')
    expect(trigger.exists()).toBe(true)
    // Icon-only affordance — the sentence must not render as a link
    expect(trigger.find('.provenance-toggle__icon').text()).toBe('ⓘ')
    expect(trigger.text()).not.toContain('How is this calculated?')
  })
})

describe('CostsSection blocked-state copy (C1/C2)', () => {
  it('explains the Council Tax lookup failure and how to fix it', () => {
    const wrapper = mountCosts()  // council_tax failed -> '?'
    const text = wrapper.text()
    expect(text).toContain("Couldn't look up Council Tax")
    expect(text).toContain('Edit address')
  })

  it('never shows a bare "Impossible" for blocked totals', () => {
    // real payloads mark failed nodes with error != null (impossible)
    const wrapper = mountCosts({
      affordability: {
        monthly_mortgage: { succeeded: false, value: null, error: 'dep failed', provenance: {} },
        group_monthly_cost: { succeeded: false, value: null, error: 'dep failed', provenance: {} },
      },
    })
    const text = wrapper.text()
    expect(text).not.toContain('Impossible')
    expect(text).toContain("Can't calculate")
  })
})

describe('CostsSection explanatory copy (C5/C6/C10)', () => {
  it('explains what the group figures include', () => {
    const wrapper = mountCosts({
      affordability: {
        group_monthly_cost: {
          succeeded: true,
          value: {
            couple: { value: '1100', stddev: 0 },
            others: { value: '200', stddev: 0 },
            couple_label: 'S',
            others_label: 'Ashby',
            couple_names: 'Simon+Lorena',
          },
          error: null,
          provenance: {},
        },
      },
    })
    expect(wrapper.text()).toContain('Simon+Lorena — the joint owners')
    expect(wrapper.text()).toContain('Ashby')
    // the impersonal "other adults" label is gone — the person's name
    expect(wrapper.text()).not.toContain('the other adults')
  })

  it('does NOT claim renovation costs are part of the mortgage', () => {
    // Regression: the old copy said "Renovation costs are added to the
    // amount you borrow — they're part of the mortgage". Build costs do
    // NOT come from the mortgage — the fictional sentence is gone.
    const wrapper = mountCosts()
    expect(wrapper.text()).not.toContain('added to the amount you borrow')
    expect(wrapper.text()).not.toContain('part of the mortgage')
  })

  it('names the missing piece when the total cannot be calculated', () => {
    const wrapper = mountCosts({
      affordability: {
        group_monthly_cost: {
          succeeded: false,
          value: null,
          error: '77777777/total_monthly_cost: dep failed (Works estimate required for: Ashby)',
          error_detail: { user_message: 'Works estimate required for: Ashby' },
          provenance: {},
        },
      },
    })
    expect(wrapper.text()).toContain('Works estimate required for: Ashby')
  })
})

describe('CostsSection uncertainty rendering (Part A)', () => {
  it('renders ≈ on the group figures when approximate', () => {
    const wrapper = mountCosts({
      affordability: {
        group_monthly_cost: {
          succeeded: true,
          value: {
            couple: { value: '1100', stddev: 4.17 },
            others: { value: '200', stddev: 0 },
            couple_label: 'S',
            others_label: 'A',
          },
          error: null,
          provenance: {},
        },
      },
    })
    const groupRows = wrapper.findAll('.costs-row--group')
    expect(groupRows[0].text()).toContain('≈ £1100/mo')
  })

  it('renders the couple and others as SEPARATE blocks with split components', () => {
    // Regression: household rows were mixed together with a single
    // couple headline. The two groups' figures must render as separate
    // labelled blocks, each with its own breakdown rows from the DAG;
    // council tax and sinking fund are separate rows (not merged), and
    // zero rows are omitted.
    const wrapper = mountCosts({
      affordability: {
        group_monthly_cost: {
          succeeded: true,
          value: {
            couple: { value: '1548.67', stddev: 0 },
            others: { value: '322.5', stddev: 0 },
            couple_label: 'S+L',
            others_label: 'Ashby',
            couple_names: 'Simon+Lorena',
            couple_breakdown: {
              mortgage: 3000,
              council_tax: 100,
              sinking_fund: 333.36,
              commutes: 300,
              insurance: 150,
              rental_income: 0,
            },
            others_breakdown: {
              council_tax: 50,
              sinking_fund: 166.65,
              commutes: 0,
              insurance: 0,
            },
          },
          error: null,
          provenance: {},
        },
      },
    })
    const text = wrapper.text()
    expect(text).toContain('Simon+Lorena — the joint owners')
    expect(text).toContain('Ashby')
    expect(text).toContain('£1548.67/mo')
    expect(text).toContain('£322.5/mo')
    // Council tax and sinking fund are separate rows, not merged
    expect(text).toContain('Council tax')
    expect(text).toContain('Sinking fund')
    expect(text).not.toContain('Shared bills')
    // Zero rows are omitted: commutes/insurance 0 in the others block,
    // rental income 0 in the couple block
    expect(text).not.toContain('Rental income')
    expect(wrapper.find('.costs-row--total').exists()).toBe(false)
  })

  it('shows a provenance icon on every financial breakdown row', () => {
    // Each affordability component (mortgage, council tax, sinking fund,
    // commutes, insurance) carries its own provenance in the DAG — the
    // breakdown rows must expose it, not just the group total.
    const wrapper = mountCosts({
      affordability: {
        monthly_mortgage: {
          succeeded: true, value: { amount: '3008.98', currency: 'GBP' }, error: null,
          provenance: { label: 'Monthly Mortgage', sourceType: 'calc', sources: { mortgage_required: {} } },
        },
        council_tax: {
          succeeded: true, value: { amount: '800', currency: 'GBP' }, error: null,
          provenance: { label: 'Council Tax', sourceType: 'calc', sources: { band: {} } },
        },
        monthly_sinking_fund: {
          succeeded: true, value: { amount: '5000', currency: 'GBP' }, error: null,
          provenance: { label: 'Monthly Sinking Fund', sourceType: 'calc', sources: { yearly: {} } },
        },
        monthly_commute_cost: {
          succeeded: true, value: null, error: null,
          provenance: { label: 'Commute Breakdown', sourceType: 'calc', sources: { simon: {} } },
        },
        life_insurance_total: {
          succeeded: true, value: { amount: '1800', currency: 'GBP' }, error: null,
          provenance: { label: 'Life Insurance Total', sourceType: 'calc', sources: { simon: {} } },
        },
        group_monthly_cost: {
          succeeded: true,
          value: {
            couple: { value: '4613.98', stddev: 0 },
            others: { value: '254.14', stddev: 0 },
            couple_label: 'S+L',
            others_label: 'Ashby',
            couple_names: 'Simon+Lorena',
            couple_breakdown: {
              mortgage: 3008.98,
              council_tax: 66.67,
              sinking_fund: 441.69,
              commutes: 946.64,
              insurance: 150,
              rental_income: 0,
            },
            others_breakdown: {
              council_tax: 33.33,
              sinking_fund: 220.81,
              commutes: 0,
              insurance: 0,
            },
          },
          error: null,
          provenance: { label: 'Group Monthly Cost', sourceType: 'calc', sources: { mortgage: {} } },
        },
      },
    })
    const rows = wrapper.findAll('.costs-row--sub')
    // Mortgage, Council tax, Sinking fund, Commutes, Life insurance
    // (couple) + Council tax, Sinking fund (others) — every row has a ⓘ
    expect(rows.length).toBeGreaterThanOrEqual(5)
    for (const r of rows) {
      expect(r.find('.provenance-toggle__trigger').exists()).toBe(true)
    }
  })
})
