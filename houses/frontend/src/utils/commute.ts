export function commuteColour(
    minutes: number,
    goodThreshold: number = 45,
    warnThreshold: number = 75
): 'green' | 'orange' | 'red' {
    if (minutes <= goodThreshold) return 'green'
    if (minutes <= warnThreshold) return 'orange'
    return 'red'
}
