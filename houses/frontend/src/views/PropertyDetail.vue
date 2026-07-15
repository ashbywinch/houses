<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePropertiesStore } from '../stores/properties'
import { patchTriage } from '../services/api'
import { ofstedClass } from '../utils/format'
import { commuteColour } from '../utils/commute'
import Header from '../components/Header.vue'

const route = useRoute()
const router = useRouter()
const store = usePropertiesStore()

const rid = computed(() => route.params.rid as string)
const detail = computed(() => store.details[rid.value])
const triage = computed(() => store.triage[rid.value])

watch(() => route.params.rid, (newRid) => {
  if (newRid) store.loadDetail(newRid as string)
}, { immediate: true })

// ── Section nav state ────────────────────────────────
const activeSection = ref('summary')
function scrollTo(id: string) {
  activeSection.value = id
  const el = document.getElementById('section-' + id)
  el?.scrollIntoView({ behavior: 'smooth' })
}

// ── Commute accordion state ──────────────────────────
const expandedCommutes = ref<Set<string>>(new Set())
function toggleCommute(key: string) {
  if (expandedCommutes.value.has(key)) {
    expandedCommutes.value.delete(key)
  } else {
    expandedCommutes.value.add(key)
  }
}

// ── Notes state ──────────────────────────────────────
const userNotes = ref('')
const notesSaved = ref(false)
const triageStatus = ref('')

// Initialize notes from existing triage data when detail loads
watch([triage, detail], () => {
  const t = triage.value
  if (t?.user_notes) userNotes.value = t.user_notes
  if (t?.triage_status) triageStatus.value = t.triage_status
}, { immediate: true })

async function saveNotes() {
  notesSaved.value = false
  await patchTriage(rid.value, { user_notes: userNotes.value })
  // Update local store state so it persists across page views
  if (!store.triage[rid.value]) {
    store.triage[rid.value] = { favourite: false, dismissed: false, is_viewed: false, user_notes: '', triage_status: '' }
  }
  store.triage[rid.value].user_notes = userNotes.value
  notesSaved.value = true
  setTimeout(() => { notesSaved.value = false }, 2000)
}

async function setStatus(status: string) {
  triageStatus.value = status
  await patchTriage(rid.value, { triage_status: status })
  if (store.triage[rid.value]) {
    store.triage[rid.value].triage_status = status
  }
}

async function markViewed() {
  await store.toggleTriage(rid.value, 'is_viewed', true)
}

// ── Existing computed / helpers ──────────────────────
const address = computed(() => detail.value?.best_address?.value ?? rid.value)

const price = computed(() => detail.value?.rightmove_price?.succeeded
  ? detail.value.rightmove_price.value : null)

const bedrooms = computed(() => detail.value?.rightmove_bedrooms?.succeeded
  ? detail.value.rightmove_bedrooms.value : null)

const monthlyCost = computed(() => detail.value?.affordability?.total_monthly_housing_cost?.succeeded
  ? detail.value.affordability.total_monthly_housing_cost.value : null)

// ── Phase 4.2: Surface existing data ─────────────────
const townDescription = computed(() => {
  const td = detail.value?.area?.town_description
  if (!td?.succeeded || !td.value) return null
  // The backend returns {description: string} from the LLM pipeline
  if (typeof td.value === 'string') return td.value
  if (typeof td.value === 'object' && td.value && 'description' in td.value) {
    return (td.value as Record<string, unknown>).description as string
  }
  return null
})
const walkability = computed(() =>
  detail.value?.area?.walkability?.succeeded ? detail.value.area.walkability.value : null)
const rightmoveUrl = computed(() =>
  detail.value?.rightmove_url?.succeeded ? detail.value.rightmove_url.value : null)
const bestLocation = computed(() =>
  detail.value?.location?.best_location?.succeeded ? detail.value.location.best_location.value : null)

// ── Phase 4.3: Share button ─────────────────────────
async function shareProperty() {
  const url = rightmoveUrl.value || window.location.href
  if (navigator.share) {
    await navigator.share({ title: address.value, url })
  }
}

async function toggleFavourite() {
  await store.toggleTriage(rid.value, 'favourite', !triage.value?.favourite)
}

// ── Existing helpers ─────────────────────────────────
function formatDuration(dur: unknown): string {
  if (!dur || typeof dur !== 'object') return '?'
  const d = dur as Record<string, unknown>
  const minutes = Math.round(d.value as number)
  if (minutes < 60) return `${minutes}m`
  const h = Math.floor(minutes / 60)
  const r = minutes % 60
  return r > 0 ? `${h}h${r}` : `${h}h`
}

function formatCost(cost: unknown): string {
  if (!cost || typeof cost !== 'object') return ''
  const c = cost as Record<string, unknown>
  return `£${(c.amount as number).toFixed(2)}`
}

function commuteDisplay(commute: unknown): { duration: string; cost: string } | null {
  if (!commute || typeof commute !== 'object') return null
  const c = commute as Record<string, unknown>
  if (!c.succeeded) return null
  const val = c.value as Record<string, unknown> | null
  if (!val) return null
  return {
    duration: formatDuration(val.duration),
    cost: formatCost(val.daily_cost),
  }
}

function schoolWalkMin(commutes: Record<string, unknown> | undefined, labelPart: string): number | null {
  if (!commutes) return null
  for (const [key, v] of Object.entries(commutes)) {
    if (!key.includes(labelPart)) continue
    const val = (v as Record<string, unknown>)?.value as Record<string, unknown> | undefined
    if (!val?.is_child) continue
    const dur = (val.duration as Record<string, unknown> | undefined)?.value
    return typeof dur === 'number' ? Math.round(dur) : null
  }
  return null
}

function pillColour(commute: unknown): string {
  if (!commute || typeof commute !== 'object') return 'pill--muted'
  const c = commute as Record<string, unknown>
  if (!c.succeeded) return 'pill--muted'
  const val = c.value as Record<string, unknown> | null
  if (!val) return 'pill--muted'
  const dur = val.duration as Record<string, unknown> | null
  if (!dur || typeof dur.value !== 'number') return 'pill--muted'
  const mins = dur.value
  const colour = commuteColour(
    mins,
    store.settings.commute_thresholds?.good ?? 45,
    store.settings.commute_thresholds?.warn ?? 75,
  )
  if (colour === 'green') return 'pill--good'
  if (colour === 'orange') return 'pill--warn'
  return 'pill--bad'
}

// EPC scale helper
function epcClass(band: string): string {
  const b = band.toUpperCase()
  if (b === 'A') return 'epc-step--a'
  if (b === 'B' || b === 'C') return 'epc-step--bc'
  if (b === 'D') return 'epc-step--d'
  if (b === 'E') return 'epc-step--e'
  if (b === 'F' || b === 'G') return 'epc-step--fg'
  return ''
}
</script>

<template>
  <!-- Header -->
  <Header title="Property Detail">
    <template #actions>
      <button class="btn--icon" aria-label="Back to property list" @click="router.push('/')">←</button>
    </template>
    <template #actions-right>
      <button class="btn--icon" aria-label="Share property" @click="shareProperty">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M4 12v8a2 2 0 002 2h12a2 2 0 002-2v-8M16 6l-4-4-4 4M12 2v13" />
        </svg>
      </button>
      <button class="btn--icon" :class="{ 'btn--icon--active': triage?.favourite }" aria-label="Toggle favourite" @click="toggleFavourite">
        <svg width="20" height="20" viewBox="0 0 24 24" :fill="triage?.favourite ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="2">
          <path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z" />
        </svg>
      </button>
    </template>
  </Header>

  <main class="page" role="main">
    <div v-if="store.loading && !detail" class="empty-state">
      <p class="empty-state__text">Loading property...</p>
    </div>
    <div v-else-if="store.error && !detail" class="empty-state">
      <p class="empty-state__text">Failed to load property.</p>
      <button class="btn--primary" @click="store.loadDetail(rid)">Retry</button>
      <button class="btn--secondary" @click="router.push('/')">Back to list</button>
    </div>
    <div v-else-if="!detail" class="empty-state">
      <p class="empty-state__text">Property not found.</p>
      <button class="btn--primary" @click="router.push('/')">Back to list</button>
    </div>
    <template v-else>

      <!-- Summary bar (sticky) -->
      <div class="summary-bar">
        <h1 class="summary-address">{{ address }}</h1>
        <div class="summary-row">
          <span v-if="price" class="summary-price">£{{ Number(price).toLocaleString() }}</span>
          <span v-if="monthlyCost !== null" class="summary-monthly">£{{ monthlyCost.toLocaleString() }}/mo</span>
          <span v-if="bedrooms" class="summary-bedrooms">{{ bedrooms }} bed</span>
        </div>
      </div>

      <!-- Section nav (sticky) -->
      <div class="section-nav-wrap">
        <nav class="section-nav" aria-label="Section quick navigation">
          <button v-for="s in [{id:'summary',label:'Summary'},{id:'commute',label:'Commute'},{id:'schools',label:'Schools'},{id:'costs',label:'Costs'},{id:'notes',label:'Notes'}]" :key="s.id"
            class="section-nav__tab" :class="{ 'section-nav__tab--active': activeSection === s.id }"
            @click="scrollTo(s.id)">
            {{ s.label }}
          </button>
        </nav>
      </div>

      <!-- ═══════════ SUMMARY ═══════════ -->
      <section id="section-summary" class="detail-section">
        <h2 class="detail-section__title">Summary</h2>

        <!-- Embedded map -->
        <div v-if="bestLocation" class="map-embed">
          <iframe
            :src="'https://www.openstreetmap.org/export/embed.html?bbox=' + (bestLocation.lon - 0.02) + '%2C' + (bestLocation.lat - 0.02) + '%2C' + (bestLocation.lon + 0.02) + '%2C' + (bestLocation.lat + 0.02) + '&amp;layer=mapnik&amp;marker=' + bestLocation.lat + '%2C' + bestLocation.lon"
            width="100%"
            height="180"
            style="border: 0; border-radius: 12px;"
            loading="lazy"
            referrerpolicy="no-referrer"
            title="Property location on OpenStreetMap"
          ></iframe>
        </div>
        <div v-else class="map-placeholder">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.4">
            <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z" />
            <circle cx="12" cy="10" r="3" />
          </svg>
          <span class="map-placeholder__text">No location data</span>
        </div>

        <!-- Walk score -->
        <div v-if="walkability" class="detail-field">
          <span class="detail-field__label">Walk Score</span>
          <span class="detail-field__value">
            {{ (walkability as Record<string, unknown>).walk_to_town_minutes ?? '?' }} min to town
          </span>
        </div>

        <!-- Town description -->
        <div v-if="townDescription" class="detail-field detail-field--block">
          <p class="detail-town-desc">{{ townDescription }}</p>
        </div>

        <!-- Action buttons -->
        <div class="detail-actions">
          <a v-if="rightmoveUrl" :href="rightmoveUrl" target="_blank" class="btn--primary" rel="noopener">View on Rightmove</a>
          <a v-if="bestLocation" :href="'https://www.google.com/maps/dir/' + bestLocation.lat + ',' + bestLocation.lon" target="_blank" class="btn--secondary" rel="noopener">Get Directions</a>
        </div>
      </section>

      <!-- ═══════════ COMMUTE ═══════════ -->
      <section id="section-commute" class="detail-section">
        <h2 class="detail-section__title">Commute</h2>
        <div v-for="(c, key) in detail.commutes" :key="key" class="commute-accordion">
          <button class="commute-accordion__header" @click="toggleCommute(key as string)">
            <span class="commute-accordion__label">{{ key }}</span>
            <span class="pill" :class="pillColour(c)">
              {{ commuteDisplay(c)?.duration ?? '?' }}
              {{ commuteDisplay(c)?.cost ?? '' }}
            </span>
            <span class="commute-accordion__chevron" :class="{ 'commute-accordion__chevron--open': expandedCommutes.has(key as string) }">▼</span>
          </button>
          <div v-if="expandedCommutes.has(key as string)" class="commute-accordion__body">
            <div v-if="c.value?.details?.length" class="commute-legs">
              <template v-for="(group, gi) in c.value.details!" :key="gi">
                <div v-for="(leg, li) in group.legs" :key="`${gi}-${li}`" class="commute-leg">
                  <span class="commute-leg__mode">{{ leg.mode }}</span>
                  <span class="commute-leg__duration">{{ leg.duration_minutes }} min</span>
                  <span v-if="li === 0 && group.cost != null" class="commute-leg__cost">
                    £{{ (typeof group.cost === 'number' ? group.cost : (group.cost as { amount: number }).amount).toFixed(2) }}
                  </span>
                  <span v-if="li === 0 && group.operator" class="commute-leg__operator">{{ group.operator }}</span>
                  <span v-if="leg.end_station" class="commute-leg__destination">{{ leg.end_station }}</span>
                </div>
              </template>
              <div v-if="c.value.route_description" class="commute-route">
                {{ c.value.route_description }}
              </div>
            </div>
            <div class="commute-provenance">
              {{ c.provenance?.label ?? 'unknown' }}
            </div>
          </div>
        </div>
      </section>

      <!-- ═══════════ SCHOOLS ═══════════ -->
      <section id="section-schools" class="detail-section">
        <h2 class="detail-section__title">Schools</h2>
        <div v-if="detail.schools.primary.school.succeeded" class="detail-field">
          <span class="detail-field__label">Primary</span>
          <div class="detail-field__value">
            <a :href="detail.schools.primary.school.value!.url" target="_blank">{{ detail.schools.primary.school.value!.name }}</a>
            <span class="pill pill--sm" :class="ofstedClass(detail.schools.primary.school.value!.ofsted)">{{ detail.schools.primary.school.value!.ofsted }}</span>
            <span v-if="schoolWalkMin(detail.commutes, 'Primary')" class="pill pill--sm pill--good">{{ schoolWalkMin(detail.commutes, 'Primary') }}m walk</span>
          </div>
        </div>
        <div v-if="detail.schools.secondary.school.succeeded" class="detail-field">
          <span class="detail-field__label">Secondary</span>
          <div class="detail-field__value">
            <a :href="detail.schools.secondary.school.value!.url" target="_blank">{{ detail.schools.secondary.school.value!.name }}</a>
            <span class="pill pill--sm" :class="ofstedClass(detail.schools.secondary.school.value!.ofsted)">{{ detail.schools.secondary.school.value!.ofsted }}</span>
            <span v-if="schoolWalkMin(detail.commutes, 'Secondary')" class="pill pill--sm pill--good">{{ schoolWalkMin(detail.commutes, 'Secondary') }}m walk</span>
          </div>
        </div>
      </section>

      <!-- ═══════════ COSTS ═══════════ -->
      <section id="section-costs" class="detail-section">
        <h2 class="detail-section__title">Costs</h2>

        <div class="costs-table">
          <div class="costs-row">
            <span class="costs-label">Mortgage</span>
            <span class="costs-value">£{{ detail.affordability.monthly_mortgage.value ?? '?' }}</span>
          </div>
          <div class="costs-row">
            <span class="costs-label">Council Tax</span>
            <span class="costs-value">{{ detail.affordability.council_tax.value?.band ?? '?' }} · £{{ detail.affordability.council_tax.value?.yearly_cost ?? '?' }}/yr</span>
          </div>
          <div class="costs-row">
            <span class="costs-label">Sinking Fund</span>
            <span class="costs-value">£{{ detail.affordability.monthly_sinking_fund.value ?? '?' }}</span>
          </div>
          <div class="costs-row">
            <span class="costs-label">Commute Cost</span>
            <span class="costs-value">£{{ detail.affordability.monthly_commute_cost.value?.yearly_total_gbp != null ? (detail.affordability.monthly_commute_cost.value.yearly_total_gbp / 12).toFixed(2) : '?' }}</span>
          </div>
          <div v-if="detail.affordability.monthly_commute_cost.succeeded && detail.affordability.monthly_commute_cost.value?.persons" class="costs-subsection">
            <div v-for="(cost, name) in detail.affordability.monthly_commute_cost.value.persons" :key="name" class="costs-row costs-row--sub">
              <span class="costs-label">{{ name }}</span>
              <span class="costs-value">£{{ (cost.yearly_gbp / 12).toFixed(2) }}/mo</span>
            </div>
          </div>
          <div class="costs-row costs-row--total">
            <span class="costs-label">Total Monthly</span>
            <span class="costs-value">£{{ detail.affordability.total_monthly_housing_cost.value ?? '?' }}</span>
          </div>
        </div>

        <!-- EPC scale -->
        <div v-if="detail.epc?.succeeded" class="epc-section">
          <h3 class="epc-title">EPC Rating</h3>
          <div class="epc-scale">
            <div v-for="band in ['A','B','C','D','E','F','G']" :key="band"
              class="epc-step" :class="epcClass(detail.epc.value?.band ?? '')">
              {{ band }}
              <span v-if="detail.epc.value?.band?.toUpperCase() === band" class="epc-step__marker">▲</span>
            </div>
          </div>
          <div v-if="detail.epc.value?.potential" class="epc-potential">
            Potential: {{ detail.epc.value.potential }}
          </div>
        </div>

        <!-- Stamp duty -->
        <div v-if="detail.affordability.stamp_duty" class="detail-field">
          <span class="detail-field__label">Stamp Duty</span>
          <span class="detail-field__value">£{{ detail.affordability.stamp_duty.succeeded ? detail.affordability.stamp_duty.value?.toLocaleString() : '?' }}</span>
        </div>
      </section>

      <!-- ═══════════ NOTES ═══════════ -->
      <section id="section-notes" class="detail-section">
        <h2 class="detail-section__title">Notes</h2>

        <!-- Status dropdown -->
        <div class="detail-field">
          <span class="detail-field__label">Status</span>
          <div class="detail-field__value">
            <select v-model="triageStatus" class="notes-select" @change="setStatus(triageStatus)">
              <option value="">None</option>
              <option value="shortlisted">Shortlisted</option>
              <option value="offer_made">Offer Made</option>
              <option value="rejected">Rejected</option>
            </select>
          </div>
        </div>

        <!-- Free-text notes -->
        <div class="detail-field detail-field--block">
          <span class="detail-field__label">Personal Notes</span>
          <textarea v-model="userNotes" class="notes-textarea" placeholder="Add your notes about this property..." rows="4" />
          <div class="notes-actions">
            <button class="btn--small" @click="saveNotes">Save Notes</button>
            <span v-if="notesSaved" class="notes-saved">Saved!</span>
          </div>
        </div>

        <!-- Mark as Viewed -->
        <div class="detail-field">
          <button class="btn--small btn--confirm" @click="markViewed">
            {{ triage?.is_viewed ? '✓ Viewed' : 'Mark as Viewed' }}
          </button>
        </div>

        <!-- Group notes (read-only) -->
        <div v-if="detail.comments.group_notes.value" class="detail-field detail-field--block">
          <span class="detail-field__label">Group Notes</span>
          <p class="notes-readonly">{{ detail.comments.group_notes.value }}</p>
        </div>
        <div v-if="detail.comments.ashby_comments.value" class="detail-field detail-field--block">
          <span class="detail-field__label">Ashby's Notes</span>
          <p class="notes-readonly">{{ detail.comments.ashby_comments.value }}</p>
        </div>
      </section>
    </template>
  </main>

  <!-- Bottom tab bar -->
  <nav class="tab-bar" aria-label="Main navigation">
    <button class="tab-bar__tab" @click="router.push('/')">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
      </svg>
      <span class="tab-bar__label">Properties</span>
    </button>
    <button class="tab-bar__tab" @click="router.push('/')">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z" />
      </svg>
      <span class="tab-bar__label">Favourites</span>
    </button>
    <button class="tab-bar__tab" @click="router.push('/')">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z" />
        <circle cx="12" cy="10" r="3" />
      </svg>
      <span class="tab-bar__label">Map</span>
    </button>
  </nav>
</template>

<style scoped>
.page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 0 80px;
}
.empty-state { text-align: center; padding: 60px 20px; }
.empty-state__text { font-size: 16px; color: var(--text-muted); }
.btn--primary {
  display: inline-block; padding: 0.6em 1.2em; font-size: 0.95em;
  border-radius: 6px; border: none; background: var(--blue); color: #fff;
  cursor: pointer; text-decoration: none;
}
.btn--secondary {
  display: inline-block; padding: 0.6em 1.2em; font-size: 0.95em;
  border-radius: 6px; border: 1px solid var(--border); background: var(--card-bg);
  color: var(--text); cursor: pointer; text-decoration: none;
}
.btn--small {
  font-size: 12px; padding: 8px 16px; border: 1px solid var(--border);
  border-radius: 8px; background: var(--card-bg); cursor: pointer;
  min-width: 44px; min-height: 44px;
}
.btn--confirm { background: var(--green-bg); color: var(--green); border-color: var(--green); }

/* Summary bar */
.summary-bar {
  position: sticky; top: 0; z-index: 20;
  background: var(--card-bg); padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
.summary-address { font-size: 18px; font-weight: 700; margin: 0 0 4px; color: #1a1a1a; }
.summary-row { display: flex; align-items: center; gap: 12px; }
.summary-price { font-size: 16px; font-weight: 700; color: var(--green); }
.summary-monthly { font-size: 14px; font-weight: 600; color: var(--green); margin-left: auto; }
.summary-bedrooms { font-size: 14px; color: var(--text-secondary); }

/* Section nav */
.section-nav-wrap {
  position: sticky; top: 78px; z-index: 19;
  background: var(--card-bg); border-bottom: 1px solid var(--border);
  overflow-x: auto;
}
.section-nav {
  display: flex; gap: 0;
}
.section-nav__tab {
  flex: 1; min-width: 0; padding: 10px 0;
  border: none; background: none; cursor: pointer;
  font-size: 13px; font-weight: 500; color: var(--text-secondary);
  white-space: nowrap; text-align: center;
  border-bottom: 3px solid transparent;
  min-height: 44px;
}
.section-nav__tab--active {
  color: #1a1a1a; font-weight: 700;
  border-bottom-color: #1a1a1a;
}

/* Icon buttons in header */
.btn--icon {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: rgba(255,255,255,0.12);
  color: #fff;
  font-size: 20px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0.7;
  border: none;
  cursor: pointer;
}
.btn--icon:hover { opacity: 1; background: rgba(255,255,255,0.2); }
.btn--icon--active { color: #f9a825; opacity: 1; }

/* Detail sections */
.detail-section {
  padding: 16px;
  border-bottom: 8px solid var(--page-bg);
}
.detail-section__title {
  font-size: 16px; font-weight: 700; margin: 0 0 12px;
}
.detail-field {
  display: flex; flex-wrap: wrap; align-items: baseline;
  gap: 8px; padding: 6px 0;
}
.detail-field--block { flex-direction: column; align-items: stretch; }
.detail-field__label { font-size: 13px; font-weight: 600; color: var(--text-secondary); min-width: 80px; }
.detail-field__value { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 14px; }

/* Map placeholder */
.map-placeholder {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  height: 160px; background: #e8e8e8; border-radius: 12px;
  color: #999; gap: 8px; margin-bottom: 12px;
}
.map-placeholder__text { font-size: 13px; }

.detail-town-desc { font-size: 14px; line-height: 1.6; color: var(--text-secondary); margin: 0; }

.detail-actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }

/* Commute accordions */
.commute-accordion { border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; overflow: hidden; }
.commute-accordion__header {
  display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 10px 12px; border: none; background: var(--card-bg);
  cursor: pointer; font: inherit; text-align: left;
  min-height: 44px;
}
.commute-accordion__label { font-weight: 600; font-size: 14px; flex: 1; }
.commute-accordion__chevron { font-size: 10px; color: var(--text-muted); transition: transform 0.2s; }
.commute-accordion__chevron--open { transform: rotate(180deg); }
.commute-accordion__body { padding: 8px 12px 12px; border-top: 1px solid var(--border); background: #fafafa; }
.commute-legs { display: flex; flex-direction: column; gap: 4px; }
.commute-leg { display: flex; gap: 8px; font-size: 13px; }
.commute-leg__mode { font-weight: 600; min-width: 60px; }
.commute-leg__duration { color: var(--text-secondary); }
.commute-leg__cost { color: var(--text-secondary); }
.commute-leg__operator { font-size: 12px; color: var(--text-muted); font-style: italic; }
.commute-leg__destination { font-size: 12px; color: var(--text-muted); }
.commute-route { font-size: 12px; color: var(--text-muted); font-style: italic; margin-top: 4px; }
.commute-provenance { font-size: 11px; color: var(--text-muted); margin-top: 4px; }

/* Costs table */
.costs-table { display: flex; flex-direction: column; }
.costs-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 0; border-bottom: 1px solid var(--divider);
  font-size: 14px;
}
.costs-row--sub { padding-left: 16px; font-size: 13px; color: var(--text-secondary); }
.costs-row--total { font-weight: 700; background: var(--blue-bg); margin: 0 -16px; padding: 10px 16px; border-bottom: none; border-radius: 8px; }
.costs-label { color: var(--text-secondary); }
.costs-value { font-weight: 600; }

/* EPC scale */
.epc-section { margin-top: 16px; }
.epc-title { font-size: 14px; font-weight: 600; margin: 0 0 8px; }
.epc-scale { display: flex; gap: 4px; }
.epc-step {
  flex: 1; text-align: center; padding: 8px 4px;
  border-radius: 6px; font-size: 14px; font-weight: 700; color: #fff;
  position: relative;
}
.epc-step--a { background: #2e7d32; }
.epc-step--bc { background: #1565c0; }
.epc-step--d { background: #f9a825; color: #1a1a1a; }
.epc-step--e { background: #e65100; }
.epc-step--fg { background: #c62828; }
.epc-step__marker { position: absolute; bottom: -16px; left: 50%; transform: translateX(-50%); font-size: 12px; color: #1a1a1a; }
.epc-potential { font-size: 13px; color: var(--text-secondary); margin-top: 20px; }

/* Notes */
.notes-select {
  font: inherit; padding: 8px 12px; border: 1px solid var(--border);
  border-radius: 8px; font-size: 14px; min-width: 160px; min-height: 44px;
}
.notes-textarea {
  font: inherit; padding: 8px 12px; border: 1px solid var(--border);
  border-radius: 8px; font-size: 14px; width: 100%; resize: vertical;
}
.notes-actions { display: flex; align-items: center; gap: 8px; }
.notes-saved { font-size: 13px; color: var(--green); font-weight: 600; }
.notes-readonly { font-size: 14px; color: var(--text-secondary); margin: 0; white-space: pre-wrap; }

/* Pills */
.pill { display: inline-flex; align-items: center; padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; line-height: 1.6; white-space: nowrap; }
.pill--sm { font-size: 11px; padding: 1px 7px; }
.pill--good { background: var(--green-bg); color: var(--green); }
.pill--warn { background: var(--orange-bg); color: var(--orange); }
.pill--bad { background: var(--red-bg); color: var(--red); }
.pill--muted { background: var(--muted-bg); color: var(--muted); }

/* Tab bar */
.tab-bar {
  position: fixed; bottom: 0; left: 0; right: 0; height: 56px;
  background: var(--card-bg); border-top: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-around;
  z-index: 80; padding-bottom: env(safe-area-inset-bottom, 0);
}
.tab-bar__tab {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  border: none; background: none; cursor: pointer; color: var(--text-muted);
  min-width: 56px; min-height: 44px; padding: 4px 12px;
}
.tab-bar__tab--active { color: var(--blue); }
.tab-bar__tab--active svg { stroke: var(--blue); }
.tab-bar__label { font-size: 10px; font-weight: 600; }
</style>
