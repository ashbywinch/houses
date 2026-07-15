<script setup lang="ts">
import { computed } from 'vue'
import type { PropertySummary } from '../types'
import { usePropertiesStore } from '../stores/properties'
import CommutePill from './CommutePill.vue'
import { simpleOfsted, ofstedClass } from '../utils/format'

const store = usePropertiesStore()
const props = defineProps<{
  rid: string
  data: PropertySummary
}>()

const triage = computed(() => store.triage[props.rid])

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

// Border color based on triage state
const borderClass = computed(() => {
  const t = triage.value
  if (!t) return 'card__border--active'
  if (t.dismissed) return 'card__border--dismissed'
  if (t.favourite) return 'card__border--favourite'
  if (t.is_viewed) return 'card__border--viewed'
  return 'card__border--active'
})

// EPC band color class
function epcClass(band: string | undefined): string {
  if (!band) return 'epc--muted'
  const b = band.toUpperCase()
  if (b === 'A') return 'epc--a'
  if (b === 'B' || b === 'C') return 'epc--bc'
  if (b === 'D') return 'epc--d'
  if (b === 'E') return 'epc--e'
  if (b === 'F' || b === 'G') return 'epc--fg'
  return 'epc--muted'
}

// Freshness badge — how many days ago the property was added
const freshnessDays = computed(() => {
  const f = props.data.freshness
  if (!f?.property_added_at) return null
  const then = new Date(f.property_added_at)
  const now = new Date()
  const diffMs = now.getTime() - then.getTime()
  return Math.floor(diffMs / (1000 * 60 * 60 * 24))
})

const freshnessLabel = computed(() => {
  const d = freshnessDays.value
  if (d === null) return null
  if (d === 0) return 'Added today'
  if (d === 1) return 'Added 1d ago'
  return `Added ${d}d ago`
})

const freshnessClass = computed(() => {
  const d = freshnessDays.value
  if (d === null) return 'pill--muted'
  if (d <= 7) return 'pill--good'
  if (d <= 30) return 'pill--warn'
  return 'pill--bad'
})

function commuteDuration(commute: unknown): number | null {
  const c = commute as Record<string, unknown> | undefined
  if (!c?.succeeded) return null
  const val = c.value as Record<string, unknown> | null
  if (!val) return null
  const dur = val.duration as Record<string, unknown> | null
  return dur ? Math.round(dur.value as number) : null
}

function commuteCost(commute: unknown): number | null {
  const c = commute as Record<string, unknown> | undefined
  if (!c?.succeeded) return null
  const val = c.value as Record<string, unknown> | null
  if (!val) return null
  const dailyCost = val.daily_cost as Record<string, unknown> | null
  if (!dailyCost) return null
  const amount = dailyCost.amount as number | undefined
  return amount ?? null
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
  return key.split("/").slice(1).join("/")
}

function isChildCommute(c: unknown): boolean {
  return (c as Record<string, unknown> | undefined)?.is_child === true
}

async function toggleFavourite() {
  await store.toggleTriage(props.rid, 'favourite', !triage.value?.favourite)
}

async function toggleDismissed() {
  await store.toggleTriage(props.rid, 'dismissed', !triage.value?.dismissed)
}

async function toggleViewed() {
  await store.toggleTriage(props.rid, 'is_viewed', !triage.value?.is_viewed)
}
</script>

<template>
  <article class="card" :class="{ 'card--dismissed': triage?.dismissed }">
    <!-- Left border -->
    <div class="card__border" :class="borderClass" />

    <div class="card__body">
      <!-- Row 1: Address | Monthly cost -->
      <div class="card__row card__row--top">
        <a :href="'#/property/' + rid" class="card__address" :aria-label="'View details for ' + address">
          <h3 class="card__address-text">{{ address }}</h3>
        </a>
        <span v-if="monthlyCost !== null" class="card__monthly-cost">
          £{{ monthlyCost.toLocaleString() }}/mo
        </span>
      </div>

      <!-- Row 2: Price + bedrooms | Freshness -->
      <div class="card__row card__row--specs">
        <span class="card__specs">
          <span v-if="price" class="card__price">£{{ Number(price).toLocaleString() }}</span>
          <span v-if="bedrooms" class="card__bedrooms">{{ bedrooms }} bed</span>
        </span>
        <span v-if="freshnessLabel" class="pill pill--sm" :class="freshnessClass">{{ freshnessLabel }}</span>
      </div>

      <!-- Row 3: Commutes with cost -->
      <div class="card__row card__commutes">
        <template v-for="(c, key) in data.commutes" :key="key">
          <span v-if="!isChildCommute(c.commute)" class="commute-unit">
            <span class="card__metric-label">{{ commuteLabel(c.commute, key) }}</span>
            <a v-if="location" :href="'https://www.google.com/maps/dir/' + location.lat + ',' + location.lon + '/' + encodeURIComponent(commuteLabel(c.commute, key))" class="pill-link" target="_blank" rel="noopener">
              <CommutePill :label="''" :duration="commuteDuration(c.commute)" :mode="commuteMode(c.commute)" :cost="commuteCost(c.commute)" :goodMax="commuteMode(c.commute) === 'walk' ? 15 : 45" :fineMax="commuteMode(c.commute) === 'walk' ? 30 : 75" />
            </a>
          </span>
        </template>
      </div>

      <!-- Row 4: Schools + EPC (two-column) -->
      <div v-if="data.schools || data.epc" class="card__row card__row--section card__schools-epc">
        <div class="schools-col">
          <div v-if="data.schools?.primary?.school?.succeeded" class="school-line">
            <a v-if="data.schools.primary.school.value!.url" :href="data.schools.primary.school.value!.url" target="_blank" class="school__name">{{ data.schools.primary.school.value!.name }}</a>
            <span v-else class="school__name">{{ data.schools.primary.school.value!.name }}</span>
            <span class="pill pill--xs" :class="ofstedClass(data.schools.primary.school.value!.ofsted)">{{ simpleOfsted(data.schools.primary.school.value!.ofsted) }}</span>
          </div>
          <div v-if="data.schools?.secondary?.school?.succeeded" class="school-line">
            <a v-if="data.schools.secondary.school.value!.url" :href="data.schools.secondary.school.value!.url" target="_blank" class="school__name">{{ data.schools.secondary.school.value!.name }}</a>
            <span v-else class="school__name">{{ data.schools.secondary.school.value!.name }}</span>
            <span class="pill pill--xs" :class="ofstedClass(data.schools.secondary.school.value!.ofsted)">{{ simpleOfsted(data.schools.secondary.school.value!.ofsted) }}</span>
          </div>
        </div>
        <div class="epc-col">
          <div v-if="data.epc?.succeeded && data.epc.value?.band" class="epc-badge" :class="epcClass(data.epc.value.band)">
            {{ data.epc.value.band }}
          </div>
        </div>
      </div>

      <!-- Row 5: Triage action bar -->
      <div class="card__row card__row--section card__triage">
        <button class="triage-btn" :class="{ 'triage-btn--active': triage?.favourite }" @click="toggleFavourite" aria-label="Toggle favourite">
          <svg width="20" height="20" viewBox="0 0 24 24" :fill="triage?.favourite ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="2">
            <path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z" />
          </svg>
          <span class="triage-btn__label">{{ triage?.favourite ? 'Saved' : 'Save' }}</span>
        </button>
        <button class="triage-btn" :class="{ 'triage-btn--active triage-btn--danger': triage?.dismissed }" @click="toggleDismissed" aria-label="Toggle dismissed">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
          <span class="triage-btn__label">{{ triage?.dismissed ? 'Dismissed' : 'Dismiss' }}</span>
        </button>
        <button class="triage-btn" :class="{ 'triage-btn--active triage-btn--confirm': triage?.is_viewed }" @click="toggleViewed" aria-label="Toggle viewed">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="20 6 9 17 4 12" />
          </svg>
          <span class="triage-btn__label">{{ triage?.is_viewed ? 'Seen' : 'Mark Seen' }}</span>
        </button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.card { position: relative; background: var(--card-bg); border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden; }
.card--dismissed { opacity: 0.6; }
.card__border { position: absolute; top: 0; left: 0; width: 4px; height: 100%; border-radius: var(--radius) 0 0 var(--radius); }
.card__border--active { background: var(--green); }
.card__border--favourite { background: #f9a825; }
.card__border--dismissed { background: var(--red); }
.card__border--viewed { background: var(--blue); }
.card__body { padding: 14px; display: flex; flex-direction: column; gap: 8px; }
.card__row { display: flex; align-items: center; gap: 8px; }
.card__row--top { align-items: flex-start; }
.card__row--section { margin-top: 4px; padding-top: 8px; border-top: 1px solid var(--divider); }
.card__address { flex: 1; min-width: 0; text-decoration: none; color: var(--blue); }
.card__address-text { font-size: 15px; font-weight: 600; margin: 0; word-break: break-word; }
.card__address:hover .card__address-text { text-decoration: underline; }
.card__monthly-cost { font-size: 14px; font-weight: 700; color: var(--green); white-space: nowrap; }
.card__row--specs { justify-content: space-between; }
.card__specs { display: flex; gap: 8px; font-size: 13px; color: var(--text-secondary); }
.card__price { font-weight: 600; }
.card__bedrooms { color: var(--text-secondary); }
.card__commutes { display: flex; flex-wrap: wrap; gap: 4px; line-height: 1.6; }
.commute-unit { display: inline-flex; align-items: center; gap: 4px; }
.card__metric-label { font-size: 12px; font-weight: 600; color: var(--text); white-space: nowrap; }
.pill-link { text-decoration: none; }
.card__schools-epc { display: flex; gap: 12px; }
.schools-col { flex: 1; display: flex; flex-direction: column; gap: 4px; padding-right: 8px; border-right: 1px solid var(--divider); }
.school-line { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.school__name { font-size: 12px; color: var(--blue); text-decoration: none; }
.school__name:hover { text-decoration: underline; }
.epc-col { display: flex; align-items: center; justify-content: center; min-width: 48px; }
.epc-badge {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 800;
  color: #fff;
}
.epc--a { background: #2e7d32; }
.epc--bc { background: #1565c0; }
.epc--d { background: #f9a825; color: #1a1a1a; }
.epc--e { background: #e65100; }
.epc--fg { background: #c62828; }
.epc--muted { background: var(--muted); }

.card__triage { display: flex; justify-content: space-around; gap: 4px; }
.triage-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  min-width: 44px;
  min-height: 44px;
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--card-bg);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 11px;
  font-weight: 500;
  flex: 1;
}
.triage-btn:hover { background: #f5f5f5; }
.triage-btn--active { border-color: var(--blue); color: var(--blue); background: var(--blue-bg); }
.triage-btn--danger.triage-btn--active { border-color: var(--red); color: var(--red); background: var(--red-bg); }
.triage-btn--confirm.triage-btn--active { border-color: var(--green); color: var(--green); background: var(--green-bg); }
.triage-btn__label { font-size: 10px; }

.pill {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.6;
  white-space: nowrap;
}
.pill--xs { font-size: 10px; padding: 1px 6px; }
.pill--sm { font-size: 11px; padding: 1px 7px; }
.pill--good { background: var(--green-bg); color: var(--green); }
.pill--warn { background: var(--orange-bg); color: var(--orange); }
.pill--bad { background: var(--red-bg); color: var(--red); }
.pill--muted { background: var(--muted-bg); color: var(--muted); }
</style>
