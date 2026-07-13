export interface AttemptValue<T> {
  succeeded: boolean
  value: T | null
  error: string | null
  provenance: Provenance
}

export interface Provenance {
  label: string
  description?: string
  sources?: Record<string, Provenance>
}

export interface GeoPoint {
  lat: number
  lon: number
}

export interface CommuteValue {
  person?: Record<string, unknown>
  label?: string
  destination?: Record<string, unknown>
  duration?: { value: number; unit: string }
  daily_cost?: { amount: number; currency: string }
  details?: unknown[]
}

export interface SchoolValue {
  name: string
  ofsted: string
  distance_km: number
  url: string
}

export interface MonthlyCostSummary {
  simon_daily_gbp: number
  lorena_daily_gbp: number
  bracknell_daily_gbp: number
  yearly_total_gbp: number
  formula_explanation: string
}

export interface CommuteSummary {
  [key: string]: unknown
  commute: AttemptValue<CommuteValue>
}

export interface PropertySummary {
  rid: string
  best_address: AttemptValue<string>
  best_location: AttemptValue<GeoPoint>
  rightmove_price: AttemptValue<string>
  rightmove_bedrooms: AttemptValue<string>
  commutes: Record<string, CommuteSummary>
  schools: {
    primary: { school: AttemptValue<SchoolValue> }
    secondary: { school: AttemptValue<SchoolValue> }
  }
  total_monthly_cost: AttemptValue<number>
  walkability: AttemptValue<Record<string, unknown>>
}

export interface PropertyDetail {
  rid: string
  best_address: AttemptValue<string>
  rightmove_url: AttemptValue<string>
  rightmove_price: AttemptValue<string>
  rightmove_bedrooms: AttemptValue<string>
  postcode: AttemptValue<string>
  location: {
    best_location: AttemptValue<GeoPoint>
    geocode: AttemptValue<GeoPoint>
    rightmove_location: AttemptValue<GeoPoint>
    precise_location: AttemptValue<GeoPoint>
  }
  commutes: Record<string, AttemptValue<CommuteValue>>
  schools: {
    primary: { school: AttemptValue<SchoolValue> }
    secondary: { school: AttemptValue<SchoolValue> }
  }
  affordability: {
    council_tax: AttemptValue<Record<string, unknown>>
    monthly_mortgage: AttemptValue<number>
    monthly_sinking_fund: AttemptValue<number>
    monthly_commute_cost: AttemptValue<MonthlyCostSummary>
    total_monthly_housing_cost: AttemptValue<number>
  }
  area: {
    walkability: AttemptValue<Record<string, unknown>>
    town_description: AttemptValue<string>
  }
  comments: {
    status: AttemptValue<string>
    status_reason: AttemptValue<string>
    group_notes: AttemptValue<string>
    ashby_comments: AttemptValue<string>
    ashby_works_estimate: AttemptValue<number>
    design_needed: AttemptValue<string>
    planning_needed: AttemptValue<string>
  }
  settings: {
    persons: AttemptValue<unknown[]>
    financial: AttemptValue<Record<string, unknown>>
  }
}
