<script setup lang="ts">
import { computed } from 'vue'
import type { PropertySummary } from '../types'
import CommutePill from './CommutePill.vue'

const props = defineProps<{
  rid: string
  data: PropertySummary
}>()
const address = computed(() => props.data.best_address.succeeded
  ? props.data.best_address.value
  : props.rid)

const location = computed(() => props.data.best_location.succeeded
  ? props.data.best_location.value
  : null)

const price = computed(() => props.data.rightmove_price.succeeded
  ? props.data.rightmove_price.value
  : null)

const bedrooms = computed(() => props.data.rightmove_bedrooms.succeeded
  ? props.data.rightmove_bedrooms.value
  : null)

const monthlyCost = computed(() => props.data.total_monthly_cost.succeeded
  ? props.data.total_monthly_cost.value
  : null)

const mapUrl = computed(() => location.value
  ? `https://www.google.com/maps?q=${location.value.lat},${location.value.lon}`
  : null)

const rightmoveUrl = `https://www.rightmove.co.uk/properties/${props.rid}`

function dirUrl(lat: number, lon: number, dest: string): string {
  return `https://www.google.com/maps/dir/${lat},${lon}/${encodeURIComponent(dest)}`
}

function commuteDuration(commute: unknown): number | null {
  const c = commute as Record<string, unknown> | undefined
  if (!c?.succeeded) return null
  const val = c.value as Record<string, unknown> | null
  if (!val) return null
  const dur = val.duration as Record<string, unknown> | null
  return dur ? Math.round(dur.value as number) : null
}

function commuteMode(commute: unknown): string | undefined {
  const c = commute as Record<string, unknown> | undefined
  if (!c?.succeeded) return undefined
  const val = c.value as Record<string, unknown> | null
  return (val?.mode as string) || undefined
}

function commuteLabel(c: unknown, key: string): string {
  const val = (c as Record<string, unknown> | undefined)?.value as Record<string, unknown> | undefined
  if (val?.label) return val.label as string
  // Fall back to the POI label from the commute key (e.g. "Simon/Office" → "Office")
  return key.split("/").slice(1).join("/")
}

function simpleOfsted(rating: string | null): string {
  if (!rating) return ''
  // Ofsted API returns strings like "Good, Behaviour Outstanding, ..."
  // Only show the overall rating on the card
  return rating.split(',')[0].trim()
}

function ofstedClass(rating: string | null): string {
  const main = simpleOfsted(rating)
  if (main === 'Outstanding') return 'pill--good'
  if (main === 'Good') return 'pill--warn'
  if (main === 'Requires Improvement' || main === 'Inadequate') return 'pill--bad'
  return 'pill--muted'
}

function isChildCommute(c: unknown): boolean {
  const val = (c as Record<string, unknown> | undefined)?.value as Record<string, unknown> | undefined
  return val?.is_child === true
}

function schoolCommute(commutes: Record<string, { commute: unknown }> | undefined, labelPart: string): unknown | null {
  if (!commutes) return null
  for (const [key, cd] of Object.entries(commutes)) {
    if (key.includes(labelPart) && isChildCommute(cd.commute)) {
      return cd.commute
    }
  }
  return null
}
</script>

<template>
  <article class="card">
    <div class="card__border" :class="data.best_location.succeeded ? 'card__border--current' : 'card__border--dismissed'" />
    <div class="card__body">
      <div class="card__header-row">
        <a :href="'#/property/' + rid" class="card__address">{{ address }}</a>
        <a v-if="mapUrl" :href="mapUrl" target="_blank" class="card__map-link" title="View on Google Maps">🌐</a>
        <a :href="rightmoveUrl" target="_blank" class="card__external-link" title="View on Rightmove">↗</a>
        <span v-if="price" class="card__price">£{{ Number(price).toLocaleString() }}</span>
      </div>
      <div v-if="bedrooms" class="card__specs">{{ bedrooms }} bed</div>

      <!-- Walk to town -->
      <div v-if="data.walkability?.succeeded && location" class="card__row card__row--section card__commutes">
        <span class="commute-unit">
          <span class="card__metric-label">{{ (data as any).town_name?.value || (data as any).town_name || 'Town' }}</span>
          <a :href="dirUrl(location.lat, location.lon, (data as any).town_name?.value || 'Town')" class="pill-link" target="_blank" rel="noopener">
            <CommutePill :label="''" :duration="Math.round((data.walkability.value as any).walk_to_town_minutes || 0)" mode="walk" :cost="null" :goodMax="15" :fineMax="30" />
          </a>
        </span>
      </div>

      <!-- Commutes (school walks from child persons go in the schools section) -->
      <div class="card__row card__commutes">
        <template v-for="(c, key) in data.commutes" :key="key">
          <span v-if="!isChildCommute(c.commute)" class="commute-unit">
            <span class="card__metric-label">{{ commuteLabel(c.commute, key) }}</span>
            <a v-if="location" :href="dirUrl(location.lat, location.lon, commuteLabel(c.commute, key))" class="pill-link" target="_blank" rel="noopener">
              <CommutePill :label="''" :duration="commuteDuration(c.commute)" :mode="commuteMode(c.commute)" :cost="null" :goodMax="commuteMode(c.commute) === 'walk' ? 15 : 45" :fineMax="commuteMode(c.commute) === 'walk' ? 30 : 75" />
            </a>
            <CommutePill v-else :label="''" :duration="commuteDuration(c.commute)" :mode="commuteMode(c.commute)" :cost="null" />
          </span>
        </template>
      </div>


      <!-- Schools (walk times from child commutes, Ofsted last) -->
      <div v-if="data.schools" class="card__row card__row--section card__schools">
        <div v-if="data.schools.primary.school.succeeded" class="school-line">
          <a :href="data.schools.primary.school.value!.url || `https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/${rid}`" target="_blank" class="school__name">{{ data.schools.primary.school.value!.name }}</a>
          <span v-if="location && commuteDuration(schoolCommute(data.commutes, 'Primary'))" class="pill-link">
            <a :href="dirUrl(location.lat, location.lon, data.schools.primary.school.value!.name)" target="_blank" rel="noopener">
              <CommutePill :label="''" :duration="commuteDuration(schoolCommute(data.commutes, 'Primary'))" mode="walk" :cost="null" :goodMax="15" :fineMax="30" />
            </a>
          </span>
          <span class="pill pill--sm" :class="ofstedClass(data.schools.primary.school.value!.ofsted)">{{ simpleOfsted(data.schools.primary.school.value!.ofsted) }}</span>
        </div>
        <div v-if="data.schools.secondary.school.succeeded" class="school-line">
          <a :href="data.schools.secondary.school.value!.url || `https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/${rid}`" target="_blank" class="school__name">{{ data.schools.secondary.school.value!.name }}</a>
          <span v-if="location && commuteDuration(schoolCommute(data.commutes, 'Secondary'))" class="pill-link">
            <a :href="dirUrl(location.lat, location.lon, data.schools.secondary.school.value!.name)" target="_blank" rel="noopener">
              <CommutePill :label="''" :duration="commuteDuration(schoolCommute(data.commutes, 'Secondary'))" mode="walk" :cost="null" :goodMax="15" :fineMax="30" />
            </a>
          </span>
          <span class="pill pill--sm" :class="ofstedClass(data.schools.secondary.school.value!.ofsted)">{{ simpleOfsted(data.schools.secondary.school.value!.ofsted) }}</span>
        </div>
      </div>
      <!-- Monthly cost -->
      <div class="card__row card__row--section card__financial">
        <span class="card__cost-total">Total monthly: {{ monthlyCost !== null ? '£' + monthlyCost.toLocaleString() + '/mo' : 'unknown' }}</span>
      </div>
    </div>
  </article>
</template>

<style scoped>
.card { position: relative; background: var(--card-bg); border-radius: var(--radius); box-shadow: var(--shadow); }
.card__border { position: absolute; top: 0; left: 0; width: 4px; height: 100%; border-radius: var(--radius) 0 0 var(--radius); }
.card__border--current { background: var(--green); }
.card__border--dismissed { background: var(--red); }
.card__body { padding: 14px; display: flex; flex-direction: column; gap: 8px; }
.card__header-row { display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; }
.card__address { flex: 1; min-width: 0; font-size: 15px; font-weight: 600; color: #1565c0; text-decoration: underline; text-decoration-color: rgba(21,101,192,0.3); word-break: break-word; }
.card__address:hover { text-decoration-color: #1565c0; }
.card__map-link { font-size: 14px; text-decoration: none; line-height: 1; opacity: 0.6; }
.card__map-link:hover { opacity: 1; }
.card__external-link { font-size: 13px; text-decoration: none; color: var(--text-muted); line-height: 1; padding: 2px; }
.card__external-link:hover { color: var(--text); }
.card__price { font-size: 14px; font-weight: 700; color: #1565c0; margin-left: auto; }
.card__specs { font-size: 13px; color: var(--text-secondary); margin-bottom: 4px; }
.card__row { margin: 0; }
.card__row--section { margin-top: 4px; padding-top: 8px; border-top: 1px solid #eee; }
.card__commutes { display: flex; flex-wrap: wrap; gap: 4px; line-height: 1.6; }
.commute-unit { display: inline-flex; align-items: center; gap: 4px; }
.card__metric-label { font-size: 12px; font-weight: 600; color: var(--text); white-space: nowrap; }
.pill-link { text-decoration: none; }
.pill--sm { font-size: 11px; padding: 1px 7px; }
.school-line { display: flex; align-items: center; gap: 4px; margin: 2px 0; flex-wrap: wrap; }
.school__name { font-size: 12px; color: #1565c0; text-decoration: none; }
.school__name:hover { text-decoration: underline; }
.card__cost-total { font-size: 13px; color: var(--text-secondary); }
</style>
