/**
 * Money formatting helpers.
 *
 * The backend serialises all monetary ``amount`` values as **strings** with
 * 2 decimal places (e.g. ``"1800.00"``).  These helpers parse them back to
 * numbers for display formatting without losing precision.
 */

/** Parse a money amount string to a number suitable for display formatting. */
export function parseAmount(amount: string | number | undefined | null): number {
  if (amount == null) return 0
  if (typeof amount === 'number') return amount
  return parseFloat(amount)
}

/** Format a money amount string as a display string (e.g. ``"£4.50"``). */
export function formatMoney(amount: string | number | undefined | null, _currency = 'GBP'): string {
  const n = parseAmount(amount)
  if (n === 0 && amount == null) return ''
  return `£${n.toFixed(2)}`
}

/** Format a money amount string with locale formatting (e.g. ``"£500,000"``). */
export function formatMoneyLocale(amount: string | number | undefined | null, _currency = 'GBP'): string {
  const n = parseAmount(amount)
  if (n === 0 && amount == null) return ''
  return `£${n.toLocaleString('en-GB', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`
}

/** Extract the numeric amount from a ``{amount, currency}`` object or raw number. */
export function extractAmount(val: { amount: string | number; currency?: string } | number | undefined | null): number {
  if (val == null) return 0
  if (typeof val === 'number') return val
  return parseAmount(val.amount)
}
