import type { PropertyDetail, PropertySummary } from '../types'

const BASE = '/api'

export function fetchAllSummaries(): Promise<Record<string, PropertySummary>> {
  return fetch(`${BASE}/properties/all`).then(r => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    return r.json()
  })
}


export function fetchPropertyDetail(rid: string): Promise<PropertyDetail> {
  return fetch(`${BASE}/properties/${encodeURIComponent(rid)}/detail`).then(r => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    return r.json()
  })
}

export function patchAddress(rid: string, address: string): Promise<Response> {
  return fetch(`${BASE}/properties/${encodeURIComponent(rid)}/address`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ address }),
  })
}

export function patchLocation(rid: string, lat: number, lon: number): Promise<Response> {
  return fetch(`${BASE}/properties/${encodeURIComponent(rid)}/location`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lat, lon }),
  })
}

export async function fetchSettings(): Promise<Record<string, unknown>> {
  const r = await fetch(`${BASE}/settings`)
  if (!r.ok) throw new Error(`Failed to fetch settings: ${r.status}`)
  return r.json()
}

export function patchPersons(persons: unknown[]): Promise<Response> {
  return fetch(`${BASE}/settings/persons`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(persons),
  })
}

export function patchFinancial(financial: Record<string, unknown>): Promise<Response> {
  return fetch(`${BASE}/settings/financial`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(financial),
  })
}

export async function patchTriage(rid: string, data: Partial<{
  favourite: boolean;
  dismissed: boolean;
  is_viewed: boolean;
  user_notes: string;
  triage_status: string;
}>): Promise<Response> {
  return fetch(`${BASE}/properties/${encodeURIComponent(rid)}/triage`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}
