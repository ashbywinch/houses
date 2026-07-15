export function simpleOfsted(rating: string): string {
    return rating.split(',')[0].trim()
}

export function ofstedClass(rating: string): string {
    if (!rating) return 'pill--muted'
    switch (simpleOfsted(rating)) {
        case 'Outstanding': return 'pill--good'
        case 'Good':        return 'pill--warn'
        default:            return 'pill--muted'
    }
}
