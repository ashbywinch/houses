export interface AttemptValue<T> {
  succeeded: boolean
  value: T | null
  error: string | null
  provenance: Provenance
}

export interface Provenance {
  label: string
  description?: string
  url?: string
  sources?: Record<string, Provenance>
}

export interface GeoPoint {
  lat: number
  lon: number
}

export interface MoneyValue {
  amount: number
  currency: string
}

export interface JourneyLeg {
  mode: string
  duration_minutes: number
  start_station?: string
  end_station?: string
  line_name?: string
}
export interface CostGroup {
  legs: JourneyLeg[]
  operator?: string
  cost?: { amount: number; currency: string } | number | null
}
export interface CommuteValue {
  label?: string
  mode?: string
  duration?: { value: number; unit: string }
  daily_cost?: { amount: number; currency: string }
  details?: CostGroup[]
  route_description?: string
  is_child?: boolean
}

export interface SchoolValue {
  name: string
  ofsted: string
  distance_km: number
  url: string
}

export interface MonthlyCostSummary {
  yearly_total_gbp: number
  formula_explanation: string
}
export interface PersonCommuteCost {
  daily_gbp: number
  yearly_gbp: number
}
export interface CommuteBreakdown {
  persons: Record<string, PersonCommuteCost>
  yearly_total_gbp: number
  formula_explanation: string
}

export interface CommuteSummary {
  commute: AttemptValue<CommuteValue> & { is_child?: boolean }
}

export interface TriageEntry {
  favourite: boolean
  dismissed: boolean
  is_viewed: boolean
  user_notes: string
  triage_status: string
}

/** Raw triage shape returned by the API — AttemptValue-wrapped, not yet extracted */
export interface TriageResponse {
  favourite: AttemptValue<boolean>
  dismissed: AttemptValue<boolean>
  is_viewed: AttemptValue<boolean>
  user_notes: AttemptValue<string>
  triage_status: AttemptValue<string>
}

export interface PropertySummary {
  rid: string
  best_address: AttemptValue<string>
  best_location: AttemptValue<GeoPoint>
  rightmove_price: AttemptValue<MoneyValue>
  rightmove_bedrooms: AttemptValue<string>
  commutes: Record<string, CommuteSummary>
  schools: {
    primary: { school: AttemptValue<SchoolValue> }
    secondary: { school: AttemptValue<SchoolValue> }
  }
  town_name?: AttemptValue<string>
  total_monthly_cost: AttemptValue<MoneyValue>
  walkability: AttemptValue<Record<string, unknown>>
  epc?: AttemptValue<{ band: string; potential?: string }>
  triage?: TriageResponse
  freshness?: {
    property_added_at: string | null
  }
}

export interface PropertyDetail {
  rid: string
  best_address: AttemptValue<string>
  rightmove_url: AttemptValue<string>
  rightmove_price: AttemptValue<MoneyValue>
  rightmove_bedrooms: AttemptValue<string>
  postcode: AttemptValue<string>
  town_name?: AttemptValue<string>
  location: {
    best_location: AttemptValue<GeoPoint>
    geocode: AttemptValue<GeoPoint>
    rightmove_location: AttemptValue<GeoPoint>
    precise_location: AttemptValue<GeoPoint>
  }
  epc?: AttemptValue<{ band: string; potential?: string }>
  commutes: Record<string, AttemptValue<CommuteValue>>
  schools: {
    primary: { school: AttemptValue<SchoolValue> }
    secondary: { school: AttemptValue<SchoolValue> }
  }
  affordability: {
    council_tax: AttemptValue<Record<string, unknown>>
    monthly_mortgage: AttemptValue<MoneyValue>
    monthly_sinking_fund: AttemptValue<MoneyValue>
    monthly_commute_cost: AttemptValue<CommuteBreakdown>
    stamp_duty: AttemptValue<MoneyValue>
    total_monthly_housing_cost: AttemptValue<MoneyValue>
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
