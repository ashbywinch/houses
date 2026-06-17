import type { PropertyResponse } from '../types'

const BASE = '/api'

export function fetchProperty(rid: string): Promise<PropertyResponse> {
  return fetch(`${BASE}/properties/${encodeURIComponent(rid)}`).then(r => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    return r.json()
  })
}
