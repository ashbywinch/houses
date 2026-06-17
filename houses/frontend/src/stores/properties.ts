import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { PropertyResponse } from '../types'
import { fetchProperty } from '../services/api'

export const usePropertiesStore = defineStore('properties', () => {
  const rids = ref<string[]>([])
  const properties = ref<Record<string, PropertyResponse>>({})
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function loadAll() {
    loading.value = true
    error.value = null
    try {
      const resp = await fetch('/api/properties/all')
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const data: Record<string, PropertyResponse> = await resp.json()
      properties.value = data
      rids.value = Object.keys(data)
    } catch (e) {
      error.value = String(e)
    } finally {
      loading.value = false
    }
  }

  async function loadProperty(rid: string) {
    const existing = properties.value[rid]
    if (existing) return existing
    loading.value = true
    error.value = null
    try {
      const prop = await fetchProperty(rid)
      properties.value[rid] = prop
      return prop
    } catch (e) {
      error.value = String(e)
      return null
    } finally {
      loading.value = false
    }
  }

  function updateProperty(rid: string, data: PropertyResponse) {
    properties.value[rid] = data
  }

  return { rids, properties, loading, error, loadAll, loadProperty, updateProperty }
})
