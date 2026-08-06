<script setup lang="ts">
import { computed } from 'vue'
import type { PropertySummary } from '../types'
import { usePropertiesStore } from '../stores/properties'
import CommutePill from './CommutePill.vue'
import { simpleOfsted, ofstedClass } from '../formatters/format'
import { schoolWalkMin } from '../formatters/school'
import { pillColour } from '../formatters/commute'

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
  ? parseFloat(props.data.rightmove_price.value?.amount ?? '0') || null
  : null)

const bedrooms = computed(() => props.data.rightmove_bedrooms.succeeded
  ? props.data.rightmove_bedrooms.value
  : null)

const monthlyCost = computed(() => props.data.total_monthly_cost.succeeded
  ? parseFloat(props.data.total_monthly_cost.value?.value?.amount ?? '0') || null
  : null)

// Part A: an approximate total (stddev > 0) renders as "≈ £X/mo".
const monthlyCostApprox = computed(() => {
  const m = props.data.total_monthly_cost
  return m.succeeded && (m.value?.stddev ?? 0) > 0
})

// Part D: a hypothetical total from the "What if…" panel (overlaid by
// PropertyList) is marked so it is never mistaken for a real number.
const isWhatIf = computed(() => props.data.total_monthly_cost.provenance?.label === 'what-if')

// Border color based on triage state
const borderClass = computed(() => {
  const t = triage.value
  if (!t) return 'card__border--active'
  if (t.dismissed) return 'card__border--dismissed'
  if (t.favourite) return 'card__border--favourite'
  if (t.is_viewed) return 'card__border--viewed'
  return 'card__border--active'
})


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
  const amount = typeof dailyCost.amount === 'string' ? parseFloat(dailyCost.amount) : (dailyCost.amount as number | undefined)
  return amount ?? null
}

function commuteMode(commute: unknown): string | undefined {
  const c = commute as Record<string, unknown> | undefined
  if (!c?.succeeded) return undefined
  const val = c.value as Record<string, unknown> | null
  return (val?.mode as string) || undefined
}

function getSchoolWalkMinutes(labelPart: string): { value: number; unit: string } | null {
  if (!props.data.commutes) return null
  for (const [key, v] of Object.entries(props.data.commutes)) {
    if (!key.includes(labelPart)) continue
    const val = (v.commute?.value as Record<string, unknown> | undefined)
    if (!val?.is_child) continue
    const dur = val.duration as { value: number; unit: string } | undefined
    return dur ? { value: Math.round(dur.value), unit: 'minute' } : null
  }
  return null
}

function commuteLabel(c: unknown, key: string): string {
  const val = (c as Record<string, unknown> | undefined)?.value as Record<string, unknown> | undefined
  if (val?.label) return val.label as string
  return key.split("/").slice(1).join("/")
}

function commutePerson(c: unknown, key: string): string {
  const val = (c as Record<string, unknown> | undefined)?.value as Record<string, unknown> | undefined
  const person = (val?.person as Record<string, unknown> | undefined)?.name
  if (typeof person === 'string' && person) return person
  return key.split("/")[0]
}

function commuteAddress(c: unknown, key: string): string {
  const val = (c as Record<string, unknown> | undefined)?.value as Record<string, unknown> | undefined
  const address = (val?.destination as Record<string, unknown> | undefined)?.address
  return typeof address === 'string' && address ? address : commuteLabel(c, key)
}

function commuteCostTitle(c: unknown): string | undefined {
  // £100.00 is the TfL daily fare cap, not the actual ticket price —
  // say so instead of letting it read as a real fare (P2).
  if (commuteCost(c) === 100) return '£100.00 is the TfL daily maximum, not the actual fare'
  return undefined
}

const adultCommutes = computed(() => {
  if (!props.data.commutes) return {}
  return Object.fromEntries(
    Object.entries(props.data.commutes).filter(([, v]) => !isChildCommute(v.commute))
  )
})

function isChildCommute(c: unknown): boolean {
  return (c as Record<string, unknown> | undefined)?.is_child === true
}

/** C4: a commute whose destination label is no longer among the
 *  person's current POIs (renamed/removed in Settings) is stale. */
function isStaleOffice(key: string): boolean {
  const person = key.split('/')[0]
  const label = key.split('/').slice(1).join('/')
  const current = store.poiLabels[person]
  if (!current) return false
  return !current.includes(label)
}

/** Top status bar colour = the worst adult commute severity (mockup). */
const statusClass = computed(() => {
  const entries = Object.entries(adultCommutes.value)
  if (entries.length === 0) return ''
  let worst = 0 // 0 ok, 1 tight, 2 far
  for (const [, c] of entries) {
    const mode = commuteMode(c.commute)
    const cls = pillColour(c.commute, mode === 'walk' ? 15 : 45, mode === 'walk' ? 30 : 75)
    const s = cls === 'pill--good' ? 0 : cls === 'pill--warn' ? 1 : cls === 'pill--bad' ? 2 : -1
    if (s > worst) worst = s
  }
  return worst === 0 ? 'card__status--ok' : worst === 1 ? 'card__status--tight' : 'card__status--far'
})

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
    <!-- Worst-commute status bar (redesign mockup) -->
    <div class="card__status" :class="statusClass" />
    <!-- Accent border -->
    <div class="card__border" :class="borderClass" />

    <div class="card__body">
      <!-- Top row: Address | Monthly cost -->
      <div class="card__top">
        <a :href="'#/property/' + rid" class="card__address" :aria-label="'View details for ' + address">
          <h3 class="card__address-text">{{ address }}</h3>
        </a>
        <span
          v-if="monthlyCost !== null"
          class="card__monthly-cost"
          :title="monthlyCostApprox ? 'Council tax estimated — total is approximate' : undefined"
        >{{ monthlyCostApprox ? '≈' : '' }}£{{ monthlyCost.toLocaleString() }}/mo
          <span v-if="isWhatIf" class="card__whatif">what-if</span>
        </span>
        <span
          v-else
          class="card__monthly-cost card__monthly-cost--unknown"
          title="Can't calculate yet — see the property page (often Council Tax)"
        >£—/mo</span>
      </div>

      <!-- Specs row: price · bedrooms · freshness -->
      <div class="card__specs">
        <span v-if="price" class="card__price">£{{ price.toLocaleString() }}</span>
        <span v-if="bedrooms">{{ bedrooms }} bed</span>
        <span v-if="freshnessLabel" class="pill pill--sm" :class="freshnessClass">{{ freshnessLabel }}</span>
      </div>

      <!-- Commute rows: per-person -->
      <div v-if="data.commutes" class="card__commutes">
        <div v-for="(c, key) in adultCommutes" :key="key" class="card__commute-row">
          <span class="card__commute-person">{{ commutePerson(c.commute, key) }} → {{ commuteLabel(c.commute, key) }}</span>
          <span v-if="isStaleOffice(key)" class="card__commute-stale" title="This office was renamed or removed in Settings — the commute shown is from an old version">old office</span>
          <div class="card__commute-data">
            <a
              v-if="location"
              :href="'https://www.google.com/maps/dir/' + location.lat + ',' + location.lon + '/' + encodeURIComponent(commuteAddress(c.commute, key))"
              class="pill-link"
              target="_blank"
              rel="noopener"
            >
              <CommutePill
                :label="''"
                :duration="commuteDuration(c.commute)"
                :mode="commuteMode(c.commute)"
                :cost="commuteCost(c.commute)"
                :goodMax="commuteMode(c.commute) === 'walk' ? 15 : 45"
                :fineMax="commuteMode(c.commute) === 'walk' ? 30 : 75"
              />
            </a>
            <span
              v-if="commuteCost(c.commute) !== null"
              class="card__commute-cost"
              :title="commuteCostTitle(c.commute)"
            >
              £{{ commuteCost(c.commute)!.toFixed(2) }}/day{{ (commuteCost(c.commute) ?? 0) >= 100 ? ' (max)' : '' }}
            </span>
          </div>
        </div>
        <a v-if="Object.keys(adultCommutes).length > 0" href="#/settings" class="card__change-dest">Change destinations →</a>
      </div>

      <!-- Schools / EPC -->
      <div v-if="data.schools?.primary?.school?.succeeded || data.schools?.secondary?.school?.succeeded || data.epc?.succeeded" class="card__schools">
        <div v-if="data.schools?.primary?.school?.succeeded" class="card__school-row">
          <span class="card__school-type">Primary</span>
          <a v-if="data.schools.primary.school.value!.url" :href="data.schools.primary.school.value!.url" target="_blank" class="card__school-name">{{ data.schools.primary.school.value!.name }}</a>
          <span v-else class="card__school-name">{{ data.schools.primary.school.value!.name }}</span>
          <span class="pill pill--xs" :class="ofstedClass(data.schools.primary.school.value!.ofsted)">{{ simpleOfsted(data.schools.primary.school.value!.ofsted) }}</span>
          <span v-if="getSchoolWalkMinutes('Primary') !== null" class="pill pill--xs pill--slate">{{ schoolWalkMin(getSchoolWalkMinutes('Primary')) }}</span>
        </div>
        <div v-if="data.schools?.secondary?.school?.succeeded" class="card__school-row">
          <span class="card__school-type">Secondary</span>
          <a v-if="data.schools.secondary.school.value!.url" :href="data.schools.secondary.school.value!.url" target="_blank" class="card__school-name">{{ data.schools.secondary.school.value!.name }}</a>
          <span v-else class="card__school-name">{{ data.schools.secondary.school.value!.name }}</span>
          <span class="pill pill--xs" :class="ofstedClass(data.schools.secondary.school.value!.ofsted)">{{ simpleOfsted(data.schools.secondary.school.value!.ofsted) }}</span>
          <span v-if="getSchoolWalkMinutes('Secondary') !== null" class="pill pill--xs pill--slate">{{ schoolWalkMin(getSchoolWalkMinutes('Secondary')) }}</span>
        </div>
        <div v-if="data.epc?.succeeded && data.epc.value" class="card__school-row card__schools-epc">
          <span class="card__school-type">EPC</span>
          <span class="card__school-name">{{ data.epc.value.band }}</span>
        </div>
      </div>

      <!-- Triage buttons -->
      <div class="card__triage">
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
.card {
  position: relative;
  background: var(--card-bg);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--border);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: box-shadow var(--transition), transform var(--transition);
}
.card:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}
.card:focus-within {
  box-shadow: var(--shadow-md);
}
.card--dismissed { opacity: 0.6; }

.card__status {
  height: 4px;
  flex-shrink: 0;
}
.card__status--ok    { background: var(--green); }
.card__status--tight { background: var(--orange); }
.card__status--far   { background: var(--red); }

.card__border {
  position: absolute;
  top: 0; left: 0;
  width: 4px; height: 100%;
  border-radius: 0;
}
.card__border--active { background: var(--green); }
.card__border--favourite { background: var(--amber); }
.card__border--dismissed { background: var(--red); }
.card__border--viewed { background: var(--blue); }

.card__body {
  padding: var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

/* Top row */
.card__top {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-3);
}
.card__address {
  flex: 1;
  min-width: 0;
  text-decoration: none;
  color: var(--slate-800);
}
.card__address-text {
  font-size: var(--fs-base);
  font-weight: var(--fw-semibold);
  margin: 0;
  word-break: break-word;
  line-height: var(--lh-tight);
}
.card__address:hover .card__address-text { color: var(--blue); text-decoration: underline; }

.card__monthly-cost--unknown {
  color: var(--text-muted);
}
.card__monthly-cost {
  flex-shrink: 0;
  font-size: var(--fs-sm);
  font-weight: var(--fw-bold);
  color: var(--green);
  white-space: nowrap;
}
.card__whatif {
  display: inline-block;
  margin-left: 0.3rem;
  font-size: 0.65rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--blue);
  border: 1px solid var(--blue);
  border-radius: 999px;
  padding: 0.05rem 0.4rem;
  vertical-align: middle;
}

/* Specs row */
.card__specs {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}
.card__price { font-weight: var(--fw-semibold); color: var(--slate-700); }

/* Commute rows */
.card__commutes {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
}
.card__commute-stale {
  font-size: 0.65rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--orange);
  border: 1px solid var(--orange);
  border-radius: 999px;
  padding: 0.05rem 0.4rem;
  white-space: nowrap;
}
.card__change-dest {
  display: inline-block;
  margin-top: 0.3rem;
  font-size: 0.75rem;
  color: var(--blue);
  text-decoration: none;
}
.card__change-dest:hover {
  text-decoration: underline;
}
.card__commute-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-sm);
}
.card__commute-person {
  font-weight: var(--fw-medium);
  color: var(--slate-700);
  min-width: 70px;
  white-space: nowrap;
}
.card__commute-data {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  color: var(--text-secondary);
}
.card__commute-cost { color: var(--slate-500); font-size: var(--fs-xs); }
.pill-link { text-decoration: none; }

/* School rows */
.card__schools {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  padding-top: var(--sp-2);
  border-top: 1px solid var(--divider);
}
.card__school-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-sm);
}
.card__school-type {
  font-weight: var(--fw-medium);
  color: var(--slate-500);
  min-width: 70px;
  font-size: var(--fs-xs);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.card__school-name {
  color: var(--blue);
  text-decoration: none;
  font-weight: var(--fw-medium);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card__school-name:hover { text-decoration: underline; }

/* Triage buttons */
.card__triage {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--sp-2);
  padding-top: var(--sp-3);
  border-top: 1px solid var(--divider);
  margin-top: auto;
}
.triage-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  min-width: 44px;
  min-height: 44px;
  padding: var(--sp-2) var(--sp-1);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--card-bg);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 11px;
  font-weight: var(--fw-medium);
  transition: all var(--transition);
}
.triage-btn:hover { background: var(--slate-50); border-color: var(--slate-300); }
.triage-btn--active { border-color: var(--blue); color: var(--blue); background: var(--blue-bg); }
.triage-btn--danger.triage-btn--active { border-color: var(--red); color: var(--red); background: var(--red-bg); }
.triage-btn--confirm.triage-btn--active { border-color: var(--green); color: var(--green); background: var(--green-bg); }
.triage-btn__label { font-size: 10px; }

/* Pill system — scoped to the SCHOOL rows so it never overrides the
 * shared CommutePill classes (which are solid; the mockup's school
 * rating chips stay light). */
.card__school-row .pill {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  line-height: 1.6;
  white-space: nowrap;
}
.card__school-row .pill--xs { font-size: 10px; padding: 1px 5px; }
.card__school-row .pill--sm { font-size: 11px; padding: 1px 7px; }
.card__school-row .pill--good { background: var(--green-bg); color: var(--green-text); }
.card__school-row .pill--warn { background: var(--orange-bg); color: var(--orange-text); }
.card__school-row .pill--bad { background: var(--red-bg); color: var(--red-text); }
.card__school-row .pill--slate { background: var(--slate-100); color: var(--slate-600); }
</style>
