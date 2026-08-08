/** Large house-purchase amounts (sale price, mortgage, deposit, works
 *  estimates) are whole pounds — never pence. Entry is PREVENTED, never
 *  transformed: non-digit keys are blocked, non-digit pastes are
 *  rejected whole, and any leak (IME, drag-drop) reverts the field to
 *  its last valid value. Nothing is ever truncated or stripped.
 *
 *  Small recurring amounts (life insurance, rental income, commute
 *  costs) allow pence — max 2dp, per GOV.UK/HMRC money input guidance. */

/** Display form only: a stored amount like "550000.00" renders as
 *  "550000". This never touches user entry. */
export function integerPounds(value: string | undefined): string {
  if (value == null) return ''
  const dot = value.indexOf('.')
  return dot === -1 ? value : value.slice(0, dot)
}

/** Block any printable non-digit key (letters, '.', ',', symbols,
 *  space) on whole-pound fields. Named keys (Backspace, Tab, arrows,
 *  Enter) and shortcuts pass through. */
export function blockWholePoundsKey(e: KeyboardEvent): void {
  if (e.ctrlKey || e.metaKey || e.altKey) return
  if (e.key.length === 1 && !/\d/.test(e.key)) e.preventDefault()
}

/** Reject a paste unless it is PURE digits — never strip, never
 *  truncate. "£550,000" and "550000.99" are refused outright. */
export function rejectWholePoundsPaste(e: ClipboardEvent): void {
  const text = e.clipboardData?.getData('text') ?? ''
  if (!/^\d+$/.test(text)) e.preventDefault()
}

/** Backstop for leaks (IME composition, drag-drop): if the field's
 *  value is not pure digits, revert it to the last valid value whole —
 *  the invalid change is rejected, never edited into shape. */
export function wholePoundsValue(el: HTMLInputElement, fallback: string): string {
  if (!/^\d*$/.test(el.value)) {
    el.value = fallback
    return fallback
  }
  return el.value
}

/** Block keystrokes that can't produce a valid pence amount (0–2dp):
 *  exponent notation and sign; the decimal point is allowed. */
export function blockPenceKey(e: KeyboardEvent): void {
  if (['e', 'E', '+', '-'].includes(e.key)) e.preventDefault()
}

/** Normalize a pence-allowed money string to at most 2dp on blur —
 *  "150.5" stays, "150.505" rounds to "150.51" (GOV.UK: pounds and
 *  pence only). */
export function normalizePence(value: string): string {
  const dot = value.indexOf('.')
  if (dot === -1) return value
  const whole = value.slice(0, dot)
  let frac = value.slice(dot + 1)
  if (frac.length <= 2) return value
  frac = String(Math.round(Number(`0.${frac}`) * 100) / 100).slice(2)
  return `${whole}.${frac}`
}
