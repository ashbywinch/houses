import { describe, it, expect } from 'vitest'
import { integerPounds, normalizePence, wholePoundsValue } from '../formatters/money'

describe('integerPounds (display only — never used on entry)', () => {
  it('leaves whole values untouched', () => {
    expect(integerPounds('550000')).toBe('550000')
  })

  it('truncates at the decimal point for display', () => {
    expect(integerPounds('550000.00')).toBe('550000')
  })

  it('handles undefined and empty', () => {
    expect(integerPounds(undefined)).toBe('')
    expect(integerPounds('')).toBe('')
  })
})

describe('wholePoundsValue (rejects invalid entry whole — never edits it)', () => {
  function input(value: string): HTMLInputElement {
    const el = document.createElement('input')
    el.value = value
    return el
  }

  it('accepts pure digits', () => {
    const el = input('550000')
    expect(wholePoundsValue(el, '500000')).toBe('550000')
    expect(el.value).toBe('550000')
  })

  it('accepts an empty field (user clearing to retype)', () => {
    const el = input('')
    expect(wholePoundsValue(el, '550000')).toBe('')
  })

  it('reverts a pasted/IME decimal whole — does not truncate it', () => {
    const el = input('550000.99')
    expect(wholePoundsValue(el, '550000')).toBe('550000')
    expect(el.value).toBe('550000')
  })

  it('reverts letters whole — does not strip them', () => {
    const el = input('55x000')
    expect(wholePoundsValue(el, '550000')).toBe('550000')
    expect(el.value).toBe('550000')
  })
})

describe('normalizePence', () => {
  it('keeps 0-2 decimal places', () => {
    expect(normalizePence('150')).toBe('150')
    expect(normalizePence('150.5')).toBe('150.5')
    expect(normalizePence('150.50')).toBe('150.50')
  })

  it('caps at 2dp (pounds and pence only)', () => {
    expect(normalizePence('150.505')).toBe('150.51')
  })
})
