/** Format walk minutes for school display (e.g. '10m walk') */
export function schoolWalkMin(walk_minutes: { value: number; unit: string } | null): string | null {
    if (walk_minutes === null || walk_minutes === undefined) return null
    return `${walk_minutes.value}m walk`
}
