export function simpleOfsted(rating: string): string {
    if (!rating) return ''
    return rating.split(',')[0].trim()
}

export function ofstedClass(rating: string): string {
    if (!rating) return 'pill--muted'
    switch (simpleOfsted(rating)) {
        case 'Outstanding':  return 'pill--good'
        case 'Good':         return 'pill--good'
        case 'Requires Improvement': return 'pill--warn'
        case 'Inadequate':   return 'pill--bad'
        default:             return 'pill--muted'
    }
}

/** EPC band → CSS class group (e.g. 'a', 'bc', 'd', 'e', 'fg', or '') */
export function epcClass(band: string | undefined): string {
    if (!band) return ''
    const b = band.toUpperCase()
    if (b === 'A') return 'a'
    if (b === 'B' || b === 'C') return 'bc'
    if (b === 'D') return 'd'
    if (b === 'E') return 'e'
    if (b === 'F' || b === 'G') return 'fg'
    return ''
}
