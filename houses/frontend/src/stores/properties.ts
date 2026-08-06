import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { MeasurementValue, PropertyDetail, PropertySummary, TriageEntry } from '../types'
import { fetchAllSummaries, fetchPropertyDetail, fetchSettings, patchTriage } from '../services/api'

export const usePropertiesStore = defineStore('properties', () => {
  const rids = ref<string[]>([])
  const summaries = ref<Record<string, PropertySummary>>({})
  const details = ref<Record<string, PropertyDetail>>({})
  const loading = ref(false)
  const error = ref<string | null>(null)
  const settings = ref<{ commute_thresholds?: { good: number; warn: number } }>({})
  const triage = ref<Record<string, TriageEntry>>({})

  // ── What-if (Part D) ──────────────────────────────────────────
  // Hypothetical monthly totals per property while the "What if…"
  // panel is active; null when showing real numbers.
  const whatIfTotals = ref<Record<string, MeasurementValue> | null>(null)

  function applyWhatIf(results: Record<string, { succeeded: boolean; monthly_total: MeasurementValue | null }>) {
    const totals: Record<string, MeasurementValue> = {}
    for (const [rid, r] of Object.entries(results)) {
      if (r.succeeded && r.monthly_total) totals[rid] = r.monthly_total
    }
    whatIfTotals.value = Object.keys(totals).length > 0 ? totals : null
  }

  function clearWhatIf() {
    whatIfTotals.value = null
  }

  /** The monthly total to show/filter/sort by for a property: the
   * hypothetical value when the what-if is active, else the real one. */
  function monthlyTotalFor(rid: string): MeasurementValue | null {
    const wt = whatIfTotals.value?.[rid]
    if (wt) return wt
    const s = summaries.value[rid]?.total_monthly_cost
    return s?.succeeded && s.value ? s.value : null
  }

  async function loadAll() {
    loading.value = true
    error.value = null
    try {
      const data = await fetchAllSummaries()
      summaries.value = data
      rids.value = Object.keys(data)
      // Populate triage from response — extract values from AttemptValue wrappers
      for (const rid of rids.value) {
        const entry = data[rid]
        const t = entry?.triage
        if (t) {
          triage.value[rid] = {
            favourite: t.favourite?.value ?? false,
            dismissed: t.dismissed?.value ?? false,
            is_viewed: t.is_viewed?.value ?? false,
            user_notes: t.user_notes?.value ?? '',
            triage_status: t.triage_status?.value ?? '',
          }
        }
      }
    } catch (e) {
      console.error('Failed to load properties:', e)
      error.value = 'Something went wrong loading properties. Please try again.'
    } finally {
      loading.value = false
    }
  }

  async function loadSettings() {
    try {
      settings.value = await fetchSettings()
    } catch {
      // defaults used
    }
  }
  loadSettings()

  async function loadDetail(rid: string, force = false) {
    if (!force && details.value[rid]) return details.value[rid]
    loading.value = true
    error.value = null
    try {
      const detail = await fetchPropertyDetail(rid)
      details.value[rid] = detail
      return detail
    } catch (e) {
      console.error('Failed to load property detail:', e)
      error.value = 'Something went wrong loading this property. Please try again.'
      return null
    } finally {
      loading.value = false
    }
  }

  async function toggleTriage(rid: string, field: keyof TriageEntry, value: boolean | string) {
    await patchTriage(rid, { [field]: value })
    if (!triage.value[rid]) {
      triage.value[rid] = { favourite: false, dismissed: false, is_viewed: false, user_notes: '', triage_status: '' }
    }
    const t = triage.value[rid]
    if (field === 'favourite') t.favourite = value as boolean
    else if (field === 'dismissed') t.dismissed = value as boolean
    else if (field === 'is_viewed') t.is_viewed = value as boolean
    else if (field === 'user_notes') t.user_notes = value as string
    else if (field === 'triage_status') t.triage_status = value as string
  }

  function updateSummary(rid: string, data: PropertySummary) {
    summaries.value[rid] = data
  }

  function updateDetail(rid: string, data: PropertyDetail) {
    details.value[rid] = data
  }

  return {
    rids, summaries, details, triage, settings, loading, error,
    whatIfTotals, applyWhatIf, clearWhatIf, monthlyTotalFor,
    loadAll, loadDetail, updateSummary, updateDetail, toggleTriage,
  }
})
