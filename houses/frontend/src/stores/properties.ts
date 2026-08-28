import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { GroupMonthlyCost, PropertyDetail, PropertySummary, TriageEntry } from '../types'
import {
  addProperty,
  fetchAllSummaries,
  fetchPropertyDetail,
  fetchSettings,
  patchPropertyDetails,
  patchTriage,
  removeProperty,
  retryScrape,
} from '../services/api'

export const usePropertiesStore = defineStore('properties', () => {
  const rids = ref<string[]>([])
  const summaries = ref<Record<string, PropertySummary>>({})
  const details = ref<Record<string, PropertyDetail>>({})
  const loading = ref(false)
  const error = ref<string | null>(null)
  const settings = ref<{ commute_thresholds?: { good: number; warn: number } }>({})
  const triage = ref<Record<string, TriageEntry>>({})
  // Per-person commute ceilings (fine_max_minutes) + current POI labels
  // (C4/C9): captured from /api/settings so cards can flag stale offices
  // and the list can hide houses over the family's ceiling.
  const commuteCeilings = ref<Record<string, { fine: number; isChild: boolean }>>({})
  // The green→amber boundary (good_max_minutes) — the 'commute colour
  // bands' — set per person in Settings and read by the card pills.
  const commuteGoods = ref<Record<string, number>>({})
  const poiLabels = ref<Record<string, string[]>>({})
  // C9: houses over the family commute ceiling are hidden only when the
  // user opts in — persisted here so the choice survives navigation.
  const showOverCeiling = ref(false)

  // Where the list page was scrolled to when the user left for a detail
  // page — restored on back-navigation. Lives in the store so it
  // survives SPA navigation but resets on a full page reload (a reload
  // starts the list at the top).
  const listScrollY = ref(0)

  // The two headline labels ("S+L", "Ashby") derived from the settings
  // persons (joint owners = current-home holders + co-owners; everyone
  // else is an other adult). Used when a property's group_monthly_cost
  // is impossible — the card still shows BOTH labelled rows, each with
  // the unknown marker, instead of collapsing to one.
  const groupLabels = ref<{ coupleLabel: string; othersLabel: string }>({ coupleLabel: '', othersLabel: '' })

  // ── What-if (Part D) ──────────────────────────────────────────
  // Hypothetical monthly totals per property while the "What if…"
  // panel is active; null when showing real numbers.
  const whatIfTotals = ref<Record<string, GroupMonthlyCost> | null>(null)

  function applyWhatIf(results: Record<string, { succeeded: boolean; group?: GroupMonthlyCost | null }>) {
    const totals: Record<string, GroupMonthlyCost> = {}
    for (const [rid, r] of Object.entries(results)) {
      if (r.succeeded && r.group) totals[rid] = r.group
    }
    whatIfTotals.value = Object.keys(totals).length > 0 ? totals : null
  }

  function clearWhatIf() {
    whatIfTotals.value = null
  }

  /** The monthly total to show/filter/sort by for a property: the
   * hypothetical value when the what-if is active, else the real one. */
  /** The couple's monthly figure (the deal-breaker) — what-if overlay
   *  when active, else the real summary value. */
  function coupleTotalFor(rid: string): number | null {
    const wt = whatIfTotals.value?.[rid]
    const g = wt ?? (summaries.value[rid]?.group_monthly_cost?.succeeded ? summaries.value[rid]?.group_monthly_cost?.value : null)
    if (!g?.couple) return null
    return Number(g.couple.value)
  }

  function groupCostFor(rid: string): GroupMonthlyCost | null {
    const wt = whatIfTotals.value?.[rid]
    if (wt) return wt
    const s = summaries.value[rid]?.group_monthly_cost
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

  interface PersonEntry {
    name: string
    is_child?: boolean
    places_of_interest?: { label: string }[]
    home_sale_price?: { amount: string }
    outstanding_mortgage?: { amount: string }
    home_co_owners?: { name: string; share?: number }[]
  }
  interface SettingsPayload {
    persons?: { value?: PersonEntry[] }
    commute_thresholds?: { value?: Record<string, { good_max_minutes?: number; fine_max_minutes?: number }> }
  }

  async function loadSettings() {
    try {
      const data = (await fetchSettings()) as SettingsPayload
      settings.value = data as unknown as { commute_thresholds?: { good: number; warn: number } }
      const thresholds = data.commute_thresholds?.value ?? {}
      const persons = data.persons?.value ?? []
      const byName = new Map(persons.map(p => [p.name, p]))
      const ceilings: Record<string, { fine: number; isChild: boolean }> = {}
      const labels: Record<string, string[]> = {}
      for (const [name, t] of Object.entries(thresholds)) {
        const p = byName.get(name)
        ceilings[name] = {
          fine: t.fine_max_minutes ?? 75,
          isChild: Boolean(p?.is_child),
        }
        commuteGoods.value[name] = t.good_max_minutes ?? 45
        labels[name] = (p?.places_of_interest ?? []).map(poi => poi.label)
      }
      commuteCeilings.value = ceilings
      poiLabels.value = labels
      // Mirror the DAG's joint_owner_names: current-home holders +
      // co-owners form the couple; every other adult is an other.
      const adults = persons.filter(p => !p.is_child)
      const money = (v?: { amount: string }) => Number(v?.amount ?? 0) > 0
      const owners = new Set<string>()
      for (const p of adults) {
        if (money(p.home_sale_price) || money(p.outstanding_mortgage) || (p.home_co_owners?.length ?? 0) > 0) {
          owners.add(p.name)
        }
        for (const co of p.home_co_owners ?? []) owners.add(co.name)
      }
      if (owners.size === 0) {
        for (const p of adults) owners.add(p.name)
      }
      groupLabels.value = {
        coupleLabel: [...adults.filter(p => owners.has(p.name))].map(p => p.name[0]?.toUpperCase() ?? '').join('+'),
        othersLabel: adults.filter(p => !owners.has(p.name)).map(p => p.name).join('+'),
      }
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

  /** Add a Rightmove URL. Returns { rid, duplicate } so the UI can jump
   * to an existing property or show the new pending card. */
  async function addByUrl(url: string): Promise<{ rid: string; duplicate: boolean }> {
    const resp = await addProperty(url)
    const rid = String(resp.rid ?? '')
    const duplicate = Boolean(resp.duplicate)
    if (rid && !duplicate) {
      // The new property is seeded server-side; refresh so the pending
      // card appears with its real scrape state.
      const data = await fetchAllSummaries()
      summaries.value = data
      rids.value = Object.keys(data)
    }
    return { rid, duplicate }
  }

  async function retryPropertyScrape(rid: string) {
    await retryScrape(rid)
    const data = await fetchAllSummaries()
    summaries.value = data
  }

  async function saveDetails(rid: string, fields: { address: string; price?: number; bedrooms?: number }) {
    await patchPropertyDetails(rid, fields)
    const data = await fetchAllSummaries()
    summaries.value = data
  }

  async function removeFromList(rid: string) {
    await removeProperty(rid)
    delete summaries.value[rid]
    const i = rids.value.indexOf(rid)
    if (i >= 0) rids.value.splice(i, 1)
  }

  function updateDetail(rid: string, data: PropertyDetail) {
    details.value[rid] = data
  }

  return {
    rids, summaries, details, triage, settings, loading, error,
    commuteCeilings, commuteGoods, poiLabels, showOverCeiling, groupLabels, listScrollY,
    addByUrl, retryPropertyScrape, saveDetails, removeFromList,
    whatIfTotals, applyWhatIf, clearWhatIf, coupleTotalFor, groupCostFor,
    loadAll, loadSettings, loadDetail, updateSummary, updateDetail, toggleTriage,
  }
})
