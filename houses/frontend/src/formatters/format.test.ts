import { describe, it, expect } from 'vitest'
import { simpleOfsted, ofstedClass } from '../formatters/format'

describe('simpleOfsted', () => {
  it('handles normal ratings', () => {
    expect(simpleOfsted('Good')).toBe('Good')
    expect(simpleOfsted('Outstanding')).toBe('Outstanding')
    expect(simpleOfsted('Requires Improvement')).toBe('Requires Improvement')
    expect(simpleOfsted('Inadequate')).toBe('Inadequate')
  })

  it('handles comma-separated rating with detail suffix', () => {
    expect(simpleOfsted('Good, inspection dated 2023')).toBe('Good')
    expect(simpleOfsted('Outstanding, Inadequate')).toBe('Outstanding')
  })

  it('handles empty string — real API data has this', () => {
    // Two properties in the live API return ofsted='' despite school.succeeded=true
    expect(simpleOfsted('')).toBe('')
  })

  it('handles null or undefined gracefully', () => {
    // value.ofsted could be null/undefined if school succeeded but data is partial
    expect(simpleOfsted(null as unknown as string)).toBe('')
    expect(simpleOfsted(undefined as unknown as string)).toBe('')
  })

  it('handles whitespace-only strings', () => {
    expect(simpleOfsted('  ')).toBe('')
    expect(simpleOfsted('  Good  ')).toBe('Good')
  })
})

describe('ofstedClass', () => {
  it('returns correct class for each rating', () => {
    expect(ofstedClass('Good')).toBe('pill--good')
    expect(ofstedClass('Outstanding')).toBe('pill--good')
    expect(ofstedClass('Requires Improvement')).toBe('pill--warn')
    expect(ofstedClass('Inadequate')).toBe('pill--bad')
    expect(ofstedClass('Unknown')).toBe('pill--muted')
  })

  it('handles empty string — real API data has this', () => {
    expect(ofstedClass('')).toBe('pill--muted')
  })

  it('handles null or undefined gracefully', () => {
    expect(ofstedClass(null as unknown as string)).toBe('pill--muted')
    expect(ofstedClass(undefined as unknown as string)).toBe('pill--muted')
  })
})
