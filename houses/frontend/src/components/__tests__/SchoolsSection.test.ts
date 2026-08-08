import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SchoolsSection from '../SchoolsSection.vue'

function mountSchools(schools: unknown, commutes: unknown = {}) {
  return mount(SchoolsSection, { props: { schools, commutes } })
}

describe('SchoolsSection school links', () => {
  const base = {
    primary: { school: { succeeded: true, value: null, error: null, provenance: { label: 'test' } } },
    secondary: { school: { succeeded: true, value: null, error: null, provenance: { label: 'test' } } },
  }

  it('links to the school website when a url exists', () => {
    const wrapper = mountSchools({
      ...base,
      primary: {
        school: {
          succeeded: true,
          value: { name: 'Chiltern Wood School', ofsted: 'Good', distance: { value: 1, unit: 'km' }, url: 'https://www.chilternwood.bucks.sch.uk' },
          error: null,
          provenance: { label: 'test' },
        },
      },
    })
    const a = wrapper.find('a')
    expect(a.exists()).toBe(true)
    expect(a.attributes('href')).toBe('https://www.chilternwood.bucks.sch.uk')
    expect(a.attributes('target')).toBe('_blank')
  })

  it('renders the school name as plain text when no url exists', () => {
    // Regression: url was '' → href="" opened the CURRENT page (the
    // property) in a new tab. No url → plain text, never a dead link.
    const wrapper = mountSchools({
      ...base,
      primary: {
        school: {
          succeeded: true,
          value: { name: 'No Website School', ofsted: 'Good', distance: { value: 1, unit: 'km' }, url: '' },
          error: null,
          provenance: { label: 'test' },
        },
      },
    })
    const a = wrapper.find('a')
    expect(a.exists()).toBe(false)
    expect(wrapper.text()).toContain('No Website School')
  })
})
