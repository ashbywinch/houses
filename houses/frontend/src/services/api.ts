import type { PropertyResponse, SettingsResponse } from '../types'

const BASE = '/api'

async function fetchJson<T>(url: string): Promise<T> {
  const resp = await fetch(url)
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${resp.statusText}`)
  return resp.json()
}

export function fetchProperties(): Promise<string[]> {
  return fetchJson<{ properties: string[] }>(`${BASE}/properties`).then(r => r.properties)
}

export function fetchProperty(rid: string): Promise<PropertyResponse> {
  return fetchJson<PropertyResponse>(`${BASE}/properties/${encodeURIComponent(rid)}`)
}

export function fetchSettings(): Promise<SettingsResponse> {
  return fetchJson<SettingsResponse>(`${BASE}/settings`)
}
