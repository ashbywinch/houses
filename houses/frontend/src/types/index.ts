export interface AttemptValue<T> {
  succeeded: boolean
  value: T | null
  error: string | null
  provenance: Provenance
}

export interface Provenance {
  label: string
  sources?: Record<string, Provenance>
}

export interface GeoPoint {
  lat: number
  lon: number
}

export interface PropertyResponse {
  rid: string
  best_address: AttemptValue<string>
  best_location: AttemptValue<GeoPoint>
}

export interface SettingsResponse {
  persons: PersonSetting[]
  commute_thresholds: Record<string, CommuteThresholds>
  bus_walk_penalty_minutes: number
}

export interface PersonSetting {
  name: string
  has_car: boolean
  deposit_equity: number | null
  places_of_interest: PlaceOfInterest[]
}

export interface PlaceOfInterest {
  label: string
  postcode: string
}

export interface CommuteThresholds {
  good_max_minutes: number
  fine_max_minutes: number
}
