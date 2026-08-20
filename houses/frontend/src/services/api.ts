import type { GroupMonthlyCost, PropertyDetail, PropertySummary } from '../types'
import { useAuthStore } from '../stores/auth'
import router from '../router'

const BASE = '/api'

function isAuthUrl(url: string): boolean {
  return url.includes('/api/auth/')
}

function checkFor401(r: Response): Response {
  if (r.status === 401 && !isAuthUrl(r.url)) {
    router.push('/login')
    throw new Error('Session expired')
  }
  return r
}

async function parseJson<T>(r: Response): Promise<T> {
  if (r.status === 204) {
    console.warn('Unexpected 204 from', r.url)
    return null as T
  }
  const text = await r.text()
  if (!text) {
    console.warn('Empty response from', r.url)
    return null as T
  }
  try {
    return JSON.parse(text) as T
  } catch (e) {
    console.error('Failed to parse JSON from', r.url, ':', text.slice(0, 200), e)
    throw new Error('Unexpected response from server')
  }
}

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
  return fetch(`${BASE}/properties/all`, { headers: { ...authHeaders() } }).then(checkFor401).then(r => parseJson(r))
}


export function fetchPropertyDetail(rid: string): Promise<PropertyDetail> {
  return fetch(`${BASE}/properties/${encodeURIComponent(rid)}/detail`, { headers: { ...authHeaders() } }).then(checkFor401).then(r => parseJson(r))
}

export function patchAddress(rid: string, address: string): Promise<Response> {
  return fetch(`${BASE}/properties/${encodeURIComponent(rid)}/address`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ address }),
  }).then(checkFor401)
}

export function patchAnnexe(
  rid: string,
  body: { payers?: string[]; ignored?: boolean },
): Promise<Response> {
  return fetch(`${BASE}/properties/${encodeURIComponent(rid)}/annexe`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  }).then(checkFor401)
}

export function patchLocation(rid: string, lat: number, lon: number): Promise<Response> {
  return fetch(`${BASE}/properties/${encodeURIComponent(rid)}/location`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ lat, lon }),
  }).then(checkFor401)
}

export async function fetchSettings(): Promise<Record<string, unknown>> {
  const r = await fetch(`${BASE}/settings`, { headers: { ...authHeaders() } }).then(checkFor401)
  if (!r.ok) throw new Error(`Failed to fetch settings: ${r.status}`)
  return parseJson(r)
}

export function patchPerson(name: string, body: Record<string, unknown>): Promise<Response> {
  return fetch(`${BASE}/settings/person/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  }).then(checkFor401)
}

export interface WhatIfResult {
  succeeded: boolean
  group: GroupMonthlyCost | null
  error?: string
}

export async function postWhatIf(persons: Record<string, unknown>[]): Promise<Record<string, WhatIfResult>> {
  const r = await fetch(`${BASE}/what-if`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ persons }),
  }).then(checkFor401)
  if (!r.ok) throw new Error(`What-if failed: ${r.status}`)
  const data = await parseJson<{ results: Record<string, WhatIfResult> }>(r)
  return data.results
}

export async function fetchCurrentHomes(): Promise<{ rid: string; address: string }[]> {
  const r = await fetch(`${BASE}/properties/current-homes`, { headers: authHeaders() })
  if (!r.ok) return []
  const data = (await parseJson<{ homes: { rid: string; address: string }[] }>(r))
  return data.homes ?? []
}

export function patchFinancial(financial: Record<string, unknown>): Promise<Response> {
  return fetch(`${BASE}/settings/financial`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(financial),
  }).then(checkFor401)
}

export function patchRentalIncome(
  rid: string,
  value: number | null,
): Promise<Response> {
  return fetch(
    `${BASE}/properties/${encodeURIComponent(rid)}/rental-income`,
    {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
      },
      body: JSON.stringify({ value }),
    },
  ).then(checkFor401)
}

export function patchWorksEstimate(
  rid: string,
  person: string,
  value: number | null,
): Promise<Response> {
  return fetch(
    `${BASE}/properties/${encodeURIComponent(rid)}/works-estimate`,
    {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
      },
      body: JSON.stringify({ person, value }),
    },
  ).then(checkFor401)
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
  }).then(checkFor401)
}

export interface CommentEntry {
  person: string
  text: string
  timestamp: string
}

export function fetchComments(rid: string): Promise<CommentEntry[]> {
  return fetch(`${BASE}/properties/${encodeURIComponent(rid)}/comments`, { headers: { ...authHeaders() } }).then(checkFor401).then(r => parseJson(r))
}

export function postComment(opts: { rid: string; text: string; person?: string }): Promise<CommentEntry> {
  const body: Record<string, string> = { text: opts.text }
  if (opts.person !== undefined) body.person = opts.person
  return fetch(`${BASE}/properties/${encodeURIComponent(opts.rid)}/comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  }).then(checkFor401).then(r => parseJson(r))
}

export async function impersonate(person: string | null): Promise<Response> {
  return fetch(`${BASE}/auth/impersonate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ person }),
  })
}
