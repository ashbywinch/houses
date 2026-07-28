import type { PropertyDetail, PropertySummary } from '../types'
import { useAuthStore } from '../stores/auth'

const BASE = '/api'

function authHeaders(): Record<string, string> {
  try {
    const store = useAuthStore()
    if (store.impersonating) {
      return { 'X-Impersonate-Person': store.impersonating }
    }
  } catch {
    // Pinia not initialized yet
  }
  return {}
}

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
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ address }),
  })
}

export function patchLocation(rid: string, lat: number, lon: number): Promise<Response> {
  return fetch(`${BASE}/properties/${encodeURIComponent(rid)}/location`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ lat, lon }),
  })
}

export async function fetchSettings(): Promise<Record<string, unknown>> {
  const r = await fetch(`${BASE}/settings`)
  if (!r.ok) throw new Error(`Failed to fetch settings: ${r.status}`)
  return r.json()
}

export function putPersons(persons: unknown[]): Promise<Response> {
  return fetch(`${BASE}/settings/persons`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(persons),
  })
}

export function patchFinancial(financial: Record<string, unknown>): Promise<Response> {
  return fetch(`${BASE}/settings/financial`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
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
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(data),
  })
}

export interface CommentEntry {
  person: string
  text: string
  timestamp: string
}

export function fetchComments(rid: string): Promise<CommentEntry[]> {
  return fetch(`${BASE}/properties/${encodeURIComponent(rid)}/comments`).then(r => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    return r.json()
  })
}

export function postComment(rid: string, text: string, person?: string): Promise<CommentEntry> {
  const body: Record<string, string> = { text }
  if (person !== undefined) body.person = person
  return fetch(`${BASE}/properties/${encodeURIComponent(rid)}/comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  }).then(r => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    return r.json()
  })
}
