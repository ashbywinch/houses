export interface AttemptValue<T> {
  succeeded: boolean
  value: T | null
  error: string | null
  errorDetail?: {
    code: string
    message: string
    userMessage: string
    retryable: boolean
    source: string
    excType: string
    traceback: string
    causes: AttemptErrorDetail[]
  }
  provenance: Provenance
}

export interface AttemptErrorDetail {
  code: string
  message: string
  userMessage: string
  retryable: boolean
  source: string
  excType: string
  traceback: string
  causes: AttemptErrorDetail[]
}

export interface FormulaLine {
  label: string
  value: string
  expression?: string
}

export interface Formula {
  lines: FormulaLine[]
  result: string
}

export interface Provenance {
  label: string
  description?: string
  url?: string
  sourceType?: "api" | "calc" | "user" | "config" | "geocode" | "db"
  freshness?: string
  status?: "impossible" | "pending" | "succeeded"
  error?: string
  expressionType?: string
  value?: unknown
  formula?: Formula
  sources?: Record<string, Provenance>
}

export interface GeoPoint {
  lat: number
  lon: number
}

export interface MoneyValue {
  amount: string
  currency: string
}

/** A value with an uncertainty — exact when stddev is absent/0 (Part A). */
export interface GroupCostValue {
  value: string
  stddev: number
}
/** Per-component monthly cost for one group (S+L vs the others) so the
 *  detail page can render the two groups' costs as separate blocks
 *  instead of mixing household rows. */
export interface GroupCostBreakdown {
  commutes: number
  insurance: number
  council_tax: number
  sinking_fund: number
  mortgage?: number
  rental_income?: number
  rent_received?: number
  rent_paid?: number
}
export interface GroupMonthlyCost {
  couple: GroupCostValue | null
  others: GroupCostValue | null
  couple_label: string
  others_label: string
  couple_names?: string
  couple_breakdown?: GroupCostBreakdown
  others_breakdown?: GroupCostBreakdown
}
export interface MeasurementValue {
  value: MoneyValue
  stddev?: number
}

export interface JourneyLeg {
  mode: string
  duration: { value: number; unit: string }
  start_station?: string
  end_station?: string
  line_name?: string
}
export interface CostGroup {
  legs: JourneyLeg[]
  operator?: string
  cost?: { amount: string; currency: string } | number | null
}
export interface CommuteValue {
  label?: string
  mode?: string
  duration?: { value: number; unit: string }
  daily_cost?: { amount: string; currency: string }
  // The API serializes the Commute model's stored field (_details), not
  // the guarded `details` property (which raises for infeasible commutes).
  _details?: CostGroup[]
  route_description?: string
  is_child?: boolean
  person?: { name?: string }
  destination?: { label?: string; address?: string }
}

export interface SchoolValue {
  name: string
  ofsted: string
  distance: { value: number; unit: string }
  url: string
  lat?: number
  lon?: number
  postcode?: string
  full_address?: string
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
  total_monthly_cost?: AttemptValue<MeasurementValue>
  group_monthly_cost: AttemptValue<GroupMonthlyCost>
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
    works_estimates: AttemptValue<Record<string, number>>
    total_works: AttemptValue<MoneyValue>
    total_equity: AttemptValue<MoneyValue>
    life_insurance_total: AttemptValue<MoneyValue>
    mortgage_required: AttemptValue<MoneyValue>
    monthly_mortgage: AttemptValue<MoneyValue>
    monthly_sinking_fund: AttemptValue<MoneyValue>
    monthly_commute_cost: AttemptValue<CommuteBreakdown>
    stamp_duty: AttemptValue<MoneyValue>
    rental_income: AttemptValue<MoneyValue>
    group_monthly_cost: AttemptValue<GroupMonthlyCost>
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
    design_needed: AttemptValue<string>
    planning_needed: AttemptValue<string>
  }
  settings: {
    persons: AttemptValue<unknown[]>
    financial: AttemptValue<Record<string, unknown>>
  }
}
