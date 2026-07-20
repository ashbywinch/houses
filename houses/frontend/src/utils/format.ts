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
