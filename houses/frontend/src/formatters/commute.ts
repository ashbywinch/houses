/** Return a colour based on commute duration */
export function commuteColour(
    minutes: number,
    goodThreshold: number = 45,
    warnThreshold: number = 75,
): 'green' | 'orange' | 'red' {
    if (minutes <= goodThreshold) return 'green'
    if (minutes <= warnThreshold) return 'orange'
    return 'red'
}

/** Format a commute duration object to a display string (e.g. '32m', '1h15') */
export function commuteDuration(dur: unknown): string {
    if (!dur || typeof dur !== 'object') return '?'
    const d = dur as Record<string, unknown>
    const minutes = Math.round(d.value as number)
    if (minutes < 60) return `${minutes}m`
    const h = Math.floor(minutes / 60)
    const r = minutes % 60
    return r > 0 ? `${h}h${r}` : `${h}h`
}

/** Format a cost object to a display string (e.g. '£4.50') */
export function commuteCost(cost: unknown): string {
    if (!cost || typeof cost !== 'object') return ''
    const c = cost as Record<string, unknown>
    const amount = typeof c.amount === 'string' ? parseFloat(c.amount) : (c.amount as number)
    return `£${amount.toFixed(2)}`
}

/** Extract mode string from a commute object */
export function commuteMode(commute: unknown): string | undefined {
    const c = commute as Record<string, unknown> | undefined
    if (!c?.succeeded) return undefined
    const val = c.value as Record<string, unknown> | null
    return (val?.mode as string) || undefined
}

/** Return a pill CSS class based on commute duration */
export function pillColour(
    commute: unknown,
    goodThreshold: number = 45,
    warnThreshold: number = 75,
): string {
    if (!commute || typeof commute !== 'object') return 'pill--muted'
    const c = commute as Record<string, unknown>
    if (!c.succeeded) return 'pill--muted'
    const val = c.value as Record<string, unknown> | null
    if (!val) return 'pill--muted'
    const dur = val.duration as Record<string, unknown> | null
    if (!dur || typeof dur.value !== 'number') return 'pill--muted'
    const mins = dur.value
    const colour = commuteColour(mins, goodThreshold, warnThreshold)
    if (colour === 'green') return 'pill--good'
    if (colour === 'orange') return 'pill--warn'
    return 'pill--bad'
}
