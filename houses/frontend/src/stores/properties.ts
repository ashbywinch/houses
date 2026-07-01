import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { PropertyDetail, PropertySummary } from '../types'
import { fetchAllSummaries, fetchPropertyDetail } from '../services/api'

export const usePropertiesStore = defineStore('properties', () => {
  const rids = ref<string[]>([])
  const summaries = ref<Record<string, PropertySummary>>({})
  const details = ref<Record<string, PropertyDetail>>({})
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function loadAll() {
    loading.value = true
    error.value = null
    try {
      const data = await fetchAllSummaries()
      summaries.value = data
      rids.value = Object.keys(data)
    } catch (e) {
      error.value = String(e)
    } finally {
      loading.value = false
    }
  }

  async function loadDetail(rid: string) {
    const existing = details.value[rid]
    if (existing) return existing
    loading.value = true
    error.value = null
    try {
      const detail = await fetchPropertyDetail(rid)
      details.value[rid] = detail
      return detail
    } catch (e) {
      error.value = String(e)
      return null
    } finally {
      loading.value = false
    }
  }

  function updateSummary(rid: string, data: PropertySummary) {
    summaries.value[rid] = data
  }

  function updateDetail(rid: string, data: PropertyDetail) {
    details.value[rid] = data
  }

  return { rids, summaries, details, loading, error, loadAll, loadDetail, updateSummary, updateDetail }
})
