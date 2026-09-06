<script setup lang="ts">
import { computed, ref } from 'vue'
import type { PropertySummary } from '../types'
import { usePropertiesStore } from '../stores/properties'
import CommutePill from './CommutePill.vue'
import { simpleOfsted, ofstedClass } from '../formatters/format'
import { schoolWalkMin } from '../formatters/school'
import { signedPounds } from '../formatters/money'

const store = usePropertiesStore()
const props = defineProps<{
  rid: string
  data: PropertySummary
}>()

const triage = computed(() => store.triage[props.rid])

const address = computed(() => props.data.best_address.succeeded
  ? props.data.best_address.value
  : props.rid)

// ── Pending-scrape state (the add-flow card) ─────────────────────────
// The card's status comes from the REAL queue state (summary.scrape),
// never a client-side fiction: pending = queued & unclaimed, in_progress
// = a worker is scraping NOW (the only 'fetching' moment), failed = the
// queue gave up. The offline escalation is honest — it is the real
// elapsed wait of an unclaimed job, computed at render.
const scrapePending = computed(() => props.data.scrape != null && !props.data.best_address.succeeded)

const listingUrl = computed(() => {
  const u = props.data.rightmove_url
  return u?.succeeded && u.value ? u.value : props.rid
})

const scrapeStatus = computed(() => {
  const s = props.data.scrape
  if (!s) return null
  if (s.status === 'in_progress') {
    return { kind: 'scraping', text: 'Fetching details now…', icon: 'spinner' }
  }
  if (s.status === 'failed') {
    return { kind: 'failed', text: "Couldn't fetch the listing — check the link", icon: 'x' }
  }
  const ageMin = (Date.now() - new Date(s.created_at).getTime()) / 60000
  if (ageMin > 2) {
    return { kind: 'offline', text: "The scraper machine is off — details arrive when it's back", icon: 'clock' }
  }
  return { kind: 'queued', text: 'On the list — details coming', icon: 'dot' }
})

const showManual = ref(false)
const manualAddress = ref('')
const manualPrice = ref('')
const manualBedrooms = ref('')

async function saveManualDetails() {
  if (!manualAddress.value.trim()) return
  await store.saveDetails(props.rid, {
    address: manualAddress.value.trim(),
    price: manualPrice.value ? Number(manualPrice.value) : undefined,
    bedrooms: manualBedrooms.value ? Number(manualBedrooms.value) : undefined,
  })
  showManual.value = false
}

async function retryScrape() {
  await store.retryPropertyScrape(props.rid)
}

async function removeThisProperty() {
  await store.removeFromList(props.rid)
}

const location = computed(() => props.data.best_location.succeeded
  ? props.data.best_location.value
  : null)

const price = computed(() => props.data.rightmove_price.succeeded
  ? parseFloat(props.data.rightmove_price.value?.amount ?? '0') || null
  : null)

const bedrooms = computed(() => props.data.rightmove_bedrooms.succeeded
  ? props.data.rightmove_bedrooms.value
  : null)

// Part A: an approximate total (stddev > 0) renders as "≈ £X/mo".
const monthlyCostApprox = computed(() => {
  const g = props.data.group_monthly_cost
  return g.succeeded && ((g.value?.couple?.stddev ?? 0) > 0 || (g.value?.others?.stddev ?? 0) > 0)
})


// The headline's TWO numbers: the joint owners (the couple) and the
// other adults, labelled dynamically — straight from the summary.
// In what-if mode the summary already IS the scenario: the server
// applied it through the DAG, so nothing is overlaid here.
const groupCost = computed(() => {
  const g = props.data.group_monthly_cost
  return g?.succeeded && g.value ? g.value : null
})
const coupleLabel = computed(() => groupCost.value?.couple_label || '')
const othersLabel = computed(() => groupCost.value?.others_label || '')
const coupleCost = computed(() => {
  const c = groupCost.value?.couple
  return c ? Number(c.value) : null
})
const othersCost = computed(() => {
  const c = groupCost.value?.others
  return c ? Number(c.value) : null
})

// ── Extra vs your home (approved deltas design) ─────────────────────
// While exactly one current home has computed totals, the server
// attaches the baseline + per-group deltas to every summary and the
// money lines show THE DELTA (never recomputed here). The current home
// itself keeps its totals and gains the baseline chip; no baseline →
// today's totals rendering, untouched.
const showDeltas = computed(() => store.baseline != null && !props.data.is_current_home)

const coupleDelta = computed(() => (showDeltas.value ? store.deltaFor(props.rid)?.couple ?? null : null))
const othersDelta = computed(() => (showDeltas.value ? store.deltaFor(props.rid)?.others ?? null : null))

function deltaLineText(d: { value: string; approx: boolean } | null): string {
  if (!d) return '—'
  return `${d.approx ? '≈' : ''}${signedPounds(d.value)}/mo`
}

/** Why a delta group is '—': the candidate side's uncomputable reason
 *  (the baseline side is always computable when a baseline is active). */
const deltaBlockedReason = computed(() => {
  const g = props.data.group_monthly_cost
  if (!g || g.succeeded) return "Can't calculate yet — see the property page (often Council Tax)"
  const detail = (g as { error_detail?: { user_message?: string } }).error_detail
  return detail?.user_message || g.error || "Can't calculate yet — see the property page (often Council Tax)"
})

const coupleLineTitle = computed(() => {
  if (!showDeltas.value) return monthlyCostApprox.value ? 'Council tax estimated — total is approximate' : undefined
  if (!coupleDelta.value) return deltaBlockedReason.value
  return coupleDelta.value.approx || monthlyCostApprox.value ? 'Council tax estimated — total is approximate' : undefined
})

const othersLineTitle = computed(() => {
  if (!showDeltas.value) return undefined
  if (!othersDelta.value) return deltaBlockedReason.value
  return othersDelta.value.approx ? 'Council tax estimated — total is approximate' : undefined
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

/** C?: the commute colour bands are the person's own thresholds
 *  (Settings → 'commute bands'), not a global constant: good = the
 *  green→amber boundary, fine = amber→red. Falls back to the walk /
 *  non-walk scales when the person has no thresholds set. */
function pillThresholds(key: string, isWalk: boolean): { goodMax: number; fineMax: number } {
  const person = key.split('/')[0]
  const good = store.commuteGoods[person]
  const fine = store.commuteCeilings[person]?.fine
  if (good != null && fine != null) return { goodMax: good, fineMax: fine }
  return isWalk ? { goodMax: 15, fineMax: 30 } : { goodMax: 45, fineMax: 75 }
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
  <article class="card" :class="[{ 'card--dismissed': triage?.dismissed }, 'card--' + (scrapeStatus?.kind ?? '')]">
    <div v-if="scrapePending && scrapeStatus" class="card__body card__body--pending">
      <div class="card__top">
        <a :href="'#/property/' + rid" class="card__address" aria-label="Open the listing">
          <h3 class="card__address-text card__address-text--url">{{ listingUrl }}</h3>
        </a>
      </div>
      <div class="card__status" :class="'card__status--' + scrapeStatus.kind">
        <span v-if="scrapeStatus.icon === 'spinner'" class="card__spinner" aria-hidden="true"></span>
        <span v-else-if="scrapeStatus.icon === 'clock'" aria-hidden="true">⏱</span>
        <span v-else-if="scrapeStatus.icon === 'x'" aria-hidden="true">✕</span>
        <span>{{ scrapeStatus.text }}</span>
      </div>
      <div class="card__actions">
        <button v-if="scrapeStatus.kind === 'failed'" class="btn btn--primary" @click="retryScrape">Retry</button>
        <button v-if="!showManual" class="btn btn--ghost" @click="showManual = true">I know the details</button>
        <button class="btn btn--ghost" @click="removeThisProperty">Remove</button>
      </div>
      <div v-if="showManual" class="card__manual">
        <input v-model="manualAddress" placeholder="Address" aria-label="Address">
        <input v-model="manualPrice" inputmode="numeric" placeholder="Price" aria-label="Price">
        <input v-model="manualBedrooms" inputmode="numeric" placeholder="Bedrooms" aria-label="Bedrooms">
        <div class="card__actions">
          <button class="btn btn--primary" @click="saveManualDetails">Save details</button>
          <button class="btn btn--ghost" @click="showManual = false">Cancel</button>
        </div>
      </div>
    </div>
    <div v-else class="card__body">
      <!-- Top row: Address | Monthly cost -->
      <div class="card__top">
        <span v-if="triage?.favourite" class="card__fav-icon" role="img" aria-label="Favourite">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2">
            <path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z" />
          </svg>
        </span>
        <a :href="'#/property/' + rid" class="card__address" :aria-label="'View details for ' + address">
          <h3 class="card__address-text">{{ address }}</h3>
        </a>
        <span v-if="store.whatIfActive" class="card__whatif">what-if</span>
        <span v-if="data.is_current_home" class="card__baseline-chip">Your home · baseline</span>
        <span v-if="coupleCost !== null || store.groupLabels.coupleLabel" class="card__monthly-cost">
          <template v-if="showDeltas">
            <span class="card__cost-line" :title="coupleLineTitle">
              <strong>{{ coupleLabel || store.groupLabels.coupleLabel }}</strong>
              {{ deltaLineText(coupleDelta) }}
            </span>
            <span v-if="othersCost !== null || store.groupLabels.othersLabel" class="card__cost-line card__cost-line--others" :title="othersLineTitle">
              <strong>{{ othersLabel || store.groupLabels.othersLabel }}</strong>
              {{ deltaLineText(othersDelta) }}
            </span>
          </template>
          <template v-else>
            <span class="card__cost-line" :title="monthlyCostApprox ? 'Council tax estimated — total is approximate' : undefined">
              <strong>{{ coupleLabel || store.groupLabels.coupleLabel }}</strong>
              {{ monthlyCostApprox ? '≈' : '' }}{{ coupleCost !== null ? '£' + coupleCost.toLocaleString() + '/mo' : '£—/mo' }}
            </span>
            <span v-if="othersCost !== null || store.groupLabels.othersLabel" class="card__cost-line card__cost-line--others">
              <strong>{{ othersLabel || store.groupLabels.othersLabel }}</strong>
              {{ othersCost !== null ? '£' + othersCost.toLocaleString() + '/mo' : '£—/mo' }}
            </span>
          </template>
        </span>
        <span
          v-else
          class="card__monthly-cost card__monthly-cost--unknown"
          title="Can't calculate yet — see the property page (often Council Tax)"
        >£—/mo</span>
      </div>

      <!-- Meta tags: price · bedrooms · freshness -->
      <div class="card__meta">
        <span v-if="price" class="card__tag card__tag--price">£{{ price.toLocaleString() }}</span>
        <span v-if="bedrooms" class="card__tag">{{ bedrooms }} bed</span>
        <span v-if="freshnessLabel" class="card__tag" :class="freshnessClass">{{ freshnessLabel }}</span>
        <span v-if="triage?.is_viewed" class="card__tag card__tag--seen">Seen</span>
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
                :goodMax="pillThresholds(key, commuteMode(c.commute) === 'walk').goodMax"
                :fineMax="pillThresholds(key, commuteMode(c.commute) === 'walk').fineMax"
              />
            </a>
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

.card__body {
  padding: 14px 14px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* Top row */
.card__top {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-3);
}
/* Favourite heart — the visible marker on favourited cards (no hover needed) */
.card__fav-icon {
  display: inline-flex;
  align-items: center;
  margin-top: 2px;
  color: var(--blue);
  flex-shrink: 0;
}
.card__fav-icon svg { display: block; }

.card__address {
  flex: 1;
  min-width: 0;
  text-decoration: none;
  color: var(--slate-800);
}
.card__address-text {
  font-size: var(--fs-lg);
  font-weight: var(--fw-semibold);
  margin: 0;
  word-break: break-word;
  line-height: 1.3;
  color: var(--text);
}
.card__address:hover .card__address-text { color: var(--blue); text-decoration: underline; }

.card__cost-line { display: block; }
.card__cost-line strong { font-weight: var(--fw-semibold); }
.card__cost-line--others { font-size: var(--fs-xs); color: var(--text-secondary); margin-top: 2px; }
.card__monthly-cost--unknown {
  color: var(--text-muted);
}
.card__monthly-cost {
  flex-shrink: 0;
  font-size: var(--fs-lg);
  font-weight: var(--fw-bold);
  color: var(--green);
  white-space: nowrap;
}
.card__whatif {
  display: inline-block;
  margin-left: 0.3rem;
  font-size: 0.65rem;
  font-weight: var(--fw-semibold);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--blue);
  border: 1px solid var(--blue);
  border-radius: var(--radius-full);
  padding: 0.05rem 0.4rem;
  vertical-align: middle;
}
.card__baseline-chip {
  flex-shrink: 0;
  font-size: 0.65rem;
  font-weight: var(--fw-semibold);
  color: var(--blue);
  border: 1px solid var(--blue);
  border-radius: var(--radius-full);
  padding: 0.05rem 0.4rem;
  white-space: nowrap;
}

/* Specs row */
.card__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.card__tag {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  font-size: var(--fs-xs);
  font-weight: var(--fw-medium);
  background: var(--pill-bg);
  color: var(--text-secondary);
}
.card__tag--price { font-weight: var(--fw-semibold); color: var(--text); }
.card__tag--seen { background: var(--slate-100); color: var(--slate-600); }
.card__tag.pill--good { background: var(--green-bg); color: var(--green-text); }
.card__tag.pill--warn { background: var(--orange-bg); color: var(--orange-text); }
.card__tag.pill--bad { background: var(--red-bg); color: var(--red-text); }
.card__tag.pill--muted { background: var(--pill-bg); color: var(--text-secondary); }

/* Commute rows */
.card__commutes {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 10px;
}
.card__commute-stale {
  font-size: 0.65rem;
  font-weight: var(--fw-semibold);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--orange);
  border: 1px solid var(--orange);
  border-radius: var(--radius-full);
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
  gap: 6px;
  font-size: var(--fs-md);
}
.card__commute-person {
  font-size: var(--fs-md);
  font-weight: var(--fw-normal);
  color: var(--text-secondary);
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.card__commute-data {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  color: var(--text-secondary);
}
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
  gap: 6px;
  font-size: var(--fs-sm);
}
.card__school-type {
  font-weight: var(--fw-semibold);
  color: var(--text-muted);
  min-width: 70px;
  font-size: var(--fs-2xs);
  text-transform: uppercase;
  letter-spacing: 0.05em;
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
  font-size: var(--fs-xs);
  font-weight: var(--fw-medium);
  transition: all var(--transition);
}
.triage-btn:hover { background: var(--slate-50); border-color: var(--slate-300); }
.triage-btn--active { border-color: var(--blue); color: var(--blue); background: var(--blue-bg); }
.triage-btn--danger.triage-btn--active { border-color: var(--red); color: var(--red); background: var(--red-bg); }
.triage-btn--confirm.triage-btn--active { border-color: var(--green); color: var(--green); background: var(--green-bg); }
.triage-btn__label { font-size: var(--fs-xs); }

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
.card__school-row .pill--xs { font-size: var(--fs-xs); padding: 1px 5px; }
.card__school-row .pill--sm { font-size: var(--fs-xs); padding: 1px 7px; }
.card__school-row .pill--good { background: var(--green-bg); color: var(--green); }
.card__school-row .pill--warn { background: var(--orange-bg); color: var(--orange-text); }
.card__school-row .pill--bad { background: var(--red-bg); color: var(--red-text); }
.card__school-row .pill--slate { background: var(--slate-100); color: var(--slate-600); margin-left: auto; }
/* ── Pending-scrape card (add-flow states) ─────────────────────────── */
.card--queued, .card--scraping { border-left: 4px solid var(--green); }
.card--offline { border-left: 4px solid var(--amber); }
.card--failed { border-left: 4px solid var(--red); }
.card__body--pending { display: flex; flex-direction: column; gap: 10px; }
.card__address-text--url { font-size: var(--fs-sm); color: var(--green-text); text-decoration: underline; word-break: break-all; }
.card__status { display: flex; align-items: center; gap: 8px; font-size: var(--fs-sm); color: var(--text-secondary); }
.card__status--failed { color: var(--red-text); }
.card__status--offline { color: var(--amber-text); }
.card__spinner { width: 14px; height: 14px; border: 2px solid var(--green-bg); border-top-color: var(--green); border-radius: 50%; animation: card-spin .8s linear infinite; }
@keyframes card-spin { to { transform: rotate(360deg); } }
.card__actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.card__actions .btn { min-height: 44px; border: none; border-radius: var(--radius-sm); padding: 8px 14px; font-size: var(--fs-sm); font-weight: 600; cursor: pointer; }
.card__actions .btn--primary { background: var(--green); color: #fff; }
.card__actions .btn--ghost { background: none; color: var(--slate-600); text-decoration: underline; padding: 8px 4px; }
.card__manual { display: flex; flex-direction: column; gap: 8px; border-top: 1px solid var(--slate-200); padding-top: 10px; }
.card__manual input { min-height: 44px; border: 1px solid var(--slate-300); border-radius: var(--radius-sm); padding: 0 12px; font-size: var(--fs-base); }
</style>
