import { describe, it, expect } from 'vitest'
import { integerPounds, normalizePence, wholePoundsFromPaste } from '../formatters/money'

describe('integerPounds', () => {
  it('leaves whole values untouched', () => {
    expect(integerPounds('550000')).toBe('550000')
  })

  it('truncates at the decimal point', () => {
    expect(integerPounds('550000.99')).toBe('550000')
  })

  it('handles undefined and empty', () => {
    expect(integerPounds(undefined)).toBe('')
    expect(integerPounds('')).toBe('')
  })
})

describe('wholePoundsFromPaste', () => {
  it('drops pence without inflating the number', () => {
    expect(wholePoundsFromPaste('550000.99')).toBe('550000')
  })

  it('strips £, commas and spaces', () => {
    expect(wholePoundsFromPaste('£550,000')).toBe('550000')
  })

  it('keeps plain digits', () => {
    expect(wholePoundsFromPaste('550000')).toBe('550000')
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
