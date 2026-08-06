/** Large house-purchase amounts (sale price, mortgage, deposit, works
 *  estimates) are whole pounds — never pence. Entry is PREVENTED at the
 *  keystroke/paste level, and the constraint is visible via helper hints
 *  on the fields. Small recurring amounts (life insurance, rental
 *  income, commute costs) allow pence — max 2dp, per GOV.UK/HMRC money
 *  input guidance. */

export function integerPounds(value: string | undefined): string {
  if (value == null) return ''
  const dot = value.indexOf('.')
  return dot === -1 ? value : value.slice(0, dot)
}

/** Backstop for whole-pound inputs: force the DOM value to the integer
 *  part even when the model is unchanged (Vue would skip the no-change
 *  patch and leave a typed decimal point visible). */
export function forceIntegerPounds(e: Event): string {
  const el = e.target as HTMLInputElement
  const clean = integerPounds(el.value)
  el.value = clean
  return clean
}

/** Block keystrokes that can't produce a whole pound amount: the decimal
 *  point (UK + US), thousands separators, exponent notation, sign. */
export function blockWholePoundsKey(e: KeyboardEvent): void {
  if (['.', ',', 'e', 'E', '+', '-'].includes(e.key)) e.preventDefault()
}

/** Block keystrokes that can't produce a valid pence amount (0–2dp):
 *  exponent notation and sign; the decimal point is allowed. */
export function blockPenceKey(e: KeyboardEvent): void {
  if (['e', 'E', '+', '-'].includes(e.key)) e.preventDefault()
}

/** Extract a whole-pound amount from pasted text: everything from the
 *  decimal point onward is dropped, then non-digits stripped — so
 *  "£550,000.99" and "550000.99" both become "550000" (never
 *  "55000099"). */
export function wholePoundsFromPaste(text: string): string {
  return integerPounds(text).replace(/[^0-9]/g, '')
}

/** Sanitize a paste into a whole-pound input. */
export function sanitizeWholePoundsPaste(e: ClipboardEvent): void {
  const text = e.clipboardData?.getData('text') ?? ''
  const clean = wholePoundsFromPaste(text)
  if (clean !== text) {
    e.preventDefault()
    insertText(e.target as HTMLInputElement, clean)
  }
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

function insertText(el: HTMLInputElement, text: string): void {
  const start = el.selectionStart ?? el.value.length
  const end = el.selectionEnd ?? el.value.length
  el.value = el.value.slice(0, start) + text + el.value.slice(end)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}
