<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, computed } from 'vue'
import { usePropertiesStore } from '../stores/properties'
import Header from '../components/Header.vue'
import PropertyCard from '../components/PropertyCard.vue'
import WhatIfPanel from '../components/WhatIfPanel.vue'
import MapView, { type MapLayer, type MapMarker } from '../components/MapView.vue'

const store = usePropertiesStore()

// ── Add a Rightmove URL (bottom sheet + tab-bar FAB) ─────────────────
const addSheetOpen = ref(false)
const addUrl = ref('')
const addError = ref('')
const addDuplicate = ref('')
const addBusy = ref(false)

function openAddSheet() {
  addError.value = ''
  addDuplicate.value = ''
  addUrl.value = ''
  addSheetOpen.value = true
}

function validateUrl(raw: string): string | null {
  const url = raw.trim()
  if (!/^https?:\/\/www\.rightmove\.co\.uk\/properties\/\d+/i.test(url)) {
    return "That doesn't look like a Rightmove link — it needs a rightmove.co.uk property URL."
  }
  return null
}

async function submitAdd() {
  const err = validateUrl(addUrl.value)
  if (err) {
    addError.value = err
    return
  }
  addBusy.value = true
  addError.value = ''
  try {
    const { rid, duplicate } = await store.addByUrl(addUrl.value.trim())
    if (duplicate) {
      addDuplicate.value = rid
      return
    }
    addSheetOpen.value = false
    activeTab.value = 'properties'
  } finally {
    addBusy.value = false
  }
}

function jumpToDuplicate() {
  addSheetOpen.value = false
  window.location.hash = '#/property/' + addDuplicate.value
}

const sortBy = ref<string>('date_added')
const showSortSheet = ref(false)
const showFilterSheet = ref(false)

/** The Sort pill shows the active choice, so it never lies about what
 *  the list is ordered by. */
const sortLabel = computed(() => sortOptions.value.find(o => o.value === sortBy.value)?.label ?? sortBy.value)
const activeTab = ref<'properties' | 'favourites' | 'map'>('properties')
const maxPriceFilter = ref<number | null>(null)
const minBedroomsFilter = ref<number | null>(null)
const maxCommuteFilter = ref<number | null>(null)
const mapFailed = ref(false)
const isochroneLayers = ref<MapLayer[]>([])

onMounted(async () => {
  // Returning from a detail page: restore where the list was (the store
  // resets on a full reload, so a fresh load starts at the top).
  const saved = store.listScrollY
  await store.loadAll()
  fetchIsochrones()
  if (saved > 0) {
    await new Promise(resolve => requestAnimationFrame(resolve))
    window.scrollTo(0, saved)
    store.listScrollY = 0
  }
})

onBeforeUnmount(() => {
  // Remember where the list was so back-navigation lands in the same
  // place. (window.scrollY at unmount — the user left via a card or the
  // tab bar; a reload never runs this.)
  store.listScrollY = window.scrollY
})

/** The isochrone polygons for the Map page — the committed toolchain
 *  artifacts (train shed, drive sheds, all-commutes intersection). */
async function fetchIsochrones() {
  try {
    const r = await fetch('/api/map/isochrones')
    if (r.ok) {
      const data = await r.json()
      isochroneLayers.value = data.layers ?? []
    }
  } catch (e) {
    console.error('Failed to load isochrone layers:', e)
  }
}

/** Property pins for the map: every house with a location. */
const mapMarkers = computed<MapMarker[]>(() =>
  allLocations().map(loc => ({
    lat: loc.lat,
    lon: loc.lon,
    label: loc.price || loc.address,
    url: '#/property/' + loc.rid,
  })),
)

/** With a baseline active the delta IS the monthly cost story (the
 *  total minus a constant orders identically), so the option relabels;
 *  the sort function itself never changes. */
const sortOptions = computed(() => [
  { value: 'price_asc', label: 'Price: Low→High' },
  { value: 'price_desc', label: 'Price: High→Low' },
  { value: 'monthly_cost', label: store.baseline ? 'Extra vs home/mo' : 'Monthly Cost' },
  { value: 'commute', label: 'Commute Time' },
  { value: 'weekly_commute', label: 'Weekly Commute (all)' },
  { value: 'ofsted', label: 'Ofsted Rating' },
  { value: 'bedrooms', label: 'Bedrooms' },
  { value: 'date_added', label: 'Date Added' },
])

function priceNum(rid: string) {
  const p = store.summaries[rid]?.rightmove_price
  if (!p?.succeeded || !p.value) return Infinity
  if (typeof p.value === 'string') return Number(p.value)
  return parseFloat(p.value.amount)
}
function bedroomNum(rid: string) {
  const b = store.summaries[rid]?.rightmove_bedrooms
  return b?.succeeded && b.value ? Number(b.value) : 0
}
function monthlyCostNum(rid: string) {
  const m = store.coupleTotalFor(rid)
  if (m == null) return Infinity
  return m
}

/** The couple's EXTRA vs the home from the summary — the "max extra
 *  vs home" filter works on this, with unknown deltas excluded, never
 *  treated as 0. */
function extraVsHomeNum(rid: string): number {
  const d = store.deltaFor(rid)?.couple
  if (!d) return Infinity
  return Number(d.value)
}

function bestCommuteMin(rid: string) {
  const commutes = store.summaries[rid]?.commutes
  if (!commutes) return Infinity
  let best = Infinity
  for (const c of Object.values(commutes)) {
    const val = c.commute?.value as Record<string, unknown> | undefined
    const dur = val?.duration as Record<string, unknown> | undefined
    const mins = dur?.value as number | undefined
    if (mins != null && mins < best) best = mins
  }
  return best
}

/** Total weekly commute time across ALL adults: each adult commutes 2
 *  trips a day, 5 days a week (child commutes don't count). */
function weeklyCommuteMin(rid: string) {
  const commutes = store.summaries[rid]?.commutes
  if (!commutes) return Infinity
  let total = 0
  for (const c of Object.values(commutes)) {
    if (c.commute?.is_child) continue
    const val = c.commute?.value as Record<string, unknown> | undefined
    const dur = val?.duration as Record<string, unknown> | undefined
    const mins = dur?.value as number | undefined
    if (mins == null) continue
    total += mins * 2 * 5
  }
  return total === 0 ? Infinity : total
}

// ── C9: per-person commute ceiling ─────────────────────────────
// Houses where ANY adult's commute exceeds their fine_max_minutes are
// hidden ONLY when the user opts in (persisted in the store so the
// choice survives navigation). The chip is always visible when there
// are over-ceiling houses, in plain language, with the count.

function overCeiling(rid: string): boolean {
  const commutes = store.summaries[rid]?.commutes
  if (!commutes) return false
  for (const [key, c] of Object.entries(commutes)) {
    const person = key.split('/')[0]
    const ceiling = store.commuteCeilings[person]
    if (!ceiling || ceiling.isChild) continue
    const val = c.commute?.value as Record<string, unknown> | undefined
    const dur = val?.duration as Record<string, unknown> | undefined
    const mins = dur?.value as number | undefined
    if (mins != null && mins > ceiling.fine) return true
  }
  return false
}

const hiddenOverCeilingCount = computed(() => store.rids.filter(overCeiling).length)

// ── C11: area / address search ─────────────────────────────────

const searchQuery = ref('')
function bestOfsted(rid: string) {
  const schools = store.summaries[rid]?.schools
  const rank: Record<string, number> = { Outstanding: 1, Good: 2, 'Requires Improvement': 3, Inadequate: 4 }
  let best = 5
  for (const key of ['primary', 'secondary'] as const) {
    const s = schools?.[key]?.school
    if (s?.succeeded && s.value?.ofsted) {
      const o = s.value.ofsted.split(',')[0].trim()
      const r = rank[o] ?? 5
      if (r < best) best = r
    }
  }
  return best
}
function addedDate(rid: string) {
  return store.summaries[rid]?.freshness?.property_added_at ?? ''
}

// ── Map helpers ───────────────────────────────────────

function allLocations() {
  const locs: { lat: number; lon: number; rid: string; address: string; price: string }[] = []
  for (const rid of store.rids) {
    const s = store.summaries[rid]
    if (s?.best_location?.succeeded && s.best_location.value) {
      const p = s.rightmove_price?.value?.amount ? `£${Number(s.rightmove_price.value.amount).toLocaleString()}` : ''
      locs.push({
        lat: s.best_location.value.lat,
        lon: s.best_location.value.lon,
        rid,
        address: s.best_address?.value ?? rid,
        price: p,
      })
    }
  }
  return locs
}

// ── Display list (filtered + sorted) ──────────────────

const displayedRids = computed(() => {
  let rids = store.rids
  if (activeTab.value === 'favourites') {
    rids = rids.filter(rid => store.triage[rid]?.favourite)
  }
  if (maxPriceFilter.value != null) {
    // With a baseline the field is "max extra vs home" — same exclusion
    // semantics (unknowns out), different measure.
    const measure = store.baseline ? extraVsHomeNum : monthlyCostNum
    rids = rids.filter(rid => measure(rid) <= maxPriceFilter.value!)
  }
  if (minBedroomsFilter.value != null) {
    rids = rids.filter(rid => bedroomNum(rid) >= minBedroomsFilter.value!)
  }
  if (maxCommuteFilter.value != null) {
    rids = rids.filter(rid => bestCommuteMin(rid) <= maxCommuteFilter.value!)
  }
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase()
    rids = rids.filter(rid => (store.summaries[rid]?.best_address?.value ?? '').toLowerCase().includes(q))
  }
  if (store.showOverCeiling) {
    rids = rids.filter(rid => !overCeiling(rid))
  }
  rids = [...rids]
  switch (sortBy.value) {
    case 'price_asc': rids.sort((a, b) => priceNum(a) - priceNum(b)); break
    case 'price_desc': rids.sort((a, b) => priceNum(b) - priceNum(a)); break
    case 'monthly_cost': rids.sort((a, b) => monthlyCostNum(a) - monthlyCostNum(b)); break
    case 'commute': rids.sort((a, b) => bestCommuteMin(a) - bestCommuteMin(b)); break
    case 'weekly_commute': rids.sort((a, b) => weeklyCommuteMin(a) - weeklyCommuteMin(b)); break
    case 'ofsted': rids.sort((a, b) => bestOfsted(a) - bestOfsted(b)); break
    case 'bedrooms': rids.sort((a, b) => bedroomNum(b) - bedroomNum(a)); break
    case 'date_added': rids.sort((a, b) => addedDate(b).localeCompare(addedDate(a))); break
  }
  return rids
})

/** Active-filter count for the Filter pill badge (mockup hierarchy 2). */
const activeFilterCount = computed(() => {
  let n = 0
  if (maxPriceFilter.value != null) n++
  if (minBedroomsFilter.value != null) n++
  if (maxCommuteFilter.value != null) n++
  if (searchQuery.value.trim()) n++
  if (store.showOverCeiling) n++
  return n
})

const ceilingLimitText = computed(() => {
  const maxFine = Math.max(0, ...Object.values(store.commuteCeilings).map(c => c.fine))
  return maxFine > 0 ? `${maxFine}-minute` : 'family'
})
</script>

<template>
  <Header title="House Hunt" />

  <!-- Map tab -->
  <div v-if="activeTab === 'map'" class="map-full">
    <MapView
      v-if="!mapFailed"
      :markers="mapMarkers"
      :layers="isochroneLayers"
      @error="mapFailed = true"
    />
    <p v-if="mapFailed" class="map-fallback-note">
      The map didn't load — your browser may block embedded maps. The pins are listed below.
    </p>
  </div>

  <!-- Properties / Favourites tab -->
  <main v-else class="page" role="main">
    <div class="search-section">
      <label class="search-bar">
        <svg class="search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          v-model="searchQuery"
          class="search-input"
          type="search"
          placeholder="Search by area or address"
          aria-label="Search by area or address"
        />
      </label>
    </div>

    <div class="controls-row">
      <button class="pill" @click="showSortSheet = true">
        <span class="pill__label">{{ sortLabel }}</span>
        <svg class="pill-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="6 9 12 15 18 9" /></svg>
      </button>
      <button class="pill" :class="{ 'pill--active': activeFilterCount > 0 }" @click="showFilterSheet = true">
        <svg class="pill-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z" /></svg>
        <span class="pill__label">Filter</span>
        <span v-if="activeFilterCount > 0" class="pill-badge">{{ activeFilterCount }}</span>
      </button>
      <span class="count-text" role="status" aria-live="polite">
        <template v-if="activeTab === 'favourites'">
          {{ displayedRids.length }} saved
        </template>
        <template v-else>{{ displayedRids.length }} found</template>
      </span>
    </div>

    <div v-if="store.showOverCeiling && hiddenOverCeilingCount > 0" class="commute-status" role="status">
      <span class="commute-status-icon" aria-hidden="true">⚠</span>
      <span>
        Hiding {{ hiddenOverCeilingCount }} house{{ hiddenOverCeilingCount === 1 ? '' : 's' }} with a commute over the
        {{ ceilingLimitText }} limit.
      </span>
      <button
        class="commute-status-dismiss"
        aria-label="Show all houses"
        @click="store.showOverCeiling = false"
      >&times;</button>
    </div>

    <div class="legend-strip" role="note" aria-label="Commute time colours">
      <span class="legend-item"><i class="legend-dot legend-dot--good"></i>fine</span>
      <span class="legend-item"><i class="legend-dot legend-dot--warn"></i>getting tight</span>
      <span class="legend-item"><i class="legend-dot legend-dot--bad"></i>yikes</span>
      <span class="legend-item"><i class="legend-dot legend-dot--muted"></i>no route</span>
    </div>

    <WhatIfPanel />

    <h2 v-if="activeTab === 'favourites'" class="tab-heading">Favourites</h2>

    <p v-if="store.baseline" class="baseline-legend" role="note" style="margin-top: var(--sp-4);">
      The house cards below show what-if numbers — {{ store.baseline.address }}
      ({{ store.groupLabels.coupleLabel }} £{{ Math.round(Number(store.baseline.couple.value)).toLocaleString() }}/mo ·
      {{ store.groupLabels.othersLabel }} {{ store.baseline.others ? '£' + Math.round(Number(store.baseline.others.value)).toLocaleString() + '/mo' : '£—/mo' }}).
      Full totals and breakdowns live on each property's page.
    </p>

    <div v-if="store.loading" class="empty-state"><p class="empty-state__text">Loading...</p></div>
    <div v-else-if="store.error" class="empty-state"><p class="empty-state__text">Error: {{ store.error }}</p></div>
    <div v-else-if="displayedRids.length === 0" class="empty-state">
      <p class="empty-state__text">
        <template v-if="store.showOverCeiling && hiddenOverCeilingCount > 0">
          Every house is hidden by the family's commute limit — show them above.
        </template>
        <template v-else>No properties match your criteria.</template>
      </p>
    </div>
    <div v-else class="card-list" role="list">
      <template v-for="rid in displayedRids" :key="rid">
        <PropertyCard :rid :data="store.summaries[rid]" />
      </template>
    </div>

    <div class="tab-bar-spacer" />

    <div v-if="showSortSheet" class="sheet-overlay" @click="showSortSheet = false" />
    <div v-if="showSortSheet" class="sheet" role="dialog" aria-label="Sort properties" aria-modal="true">
      <div class="sheet__handle" />
      <div class="sheet__header">
        <h2 class="sheet__title">Sort</h2>
        <button class="sheet__close" @click="showSortSheet = false" aria-label="Close sort">&times;</button>
      </div>
      <div class="sheet__body">
        <div class="sheet__section">
          <label class="sheet__label">Sort by</label>
          <select v-model="sortBy" class="sheet__select" @change="showSortSheet = false">
            <option v-for="opt in sortOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </div>
        <div class="sheet__section">
          <button class="sheet__apply" @click="showSortSheet = false">Apply</button>
        </div>
      </div>
    </div>
    <div v-if="showFilterSheet" class="sheet-overlay" @click="showFilterSheet = false" />
    <div v-if="showFilterSheet" class="sheet" role="dialog" aria-label="Filter properties" aria-modal="true">
      <div class="sheet__handle" />
      <div class="sheet__header">
        <h2 class="sheet__title">Filter</h2>
        <button class="sheet__close" @click="showFilterSheet = false" aria-label="Close filter">&times;</button>
      </div>
      <div class="sheet__body">
        <div class="sheet__section">
          <label class="sheet__label">{{ store.baseline ? 'Max extra vs home (£/mo)' : 'Max monthly cost (£)' }}</label>
          <input v-model.number="maxPriceFilter" type="number" class="sheet__input" placeholder="e.g. 3000" min="0" step="100" />
          <p v-if="store.baseline" class="sheet__helper">Your home is £{{ Math.round(Number(store.baseline.couple.value)).toLocaleString() }}/mo</p>
        </div>
        <div class="sheet__section">
          <label class="sheet__label">Min Bedrooms</label>
          <select v-model.number="minBedroomsFilter" class="sheet__select">
            <option :value="null">Any</option>
            <option :value="1">1+</option><option :value="2">2+</option><option :value="3">3+</option>
            <option :value="4">4+</option><option :value="5">5+</option>
          </select>
        </div>
        <div class="sheet__section">
          <label class="sheet__label">Max Commute (minutes)</label>
          <select v-model.number="maxCommuteFilter" class="sheet__select">
            <option :value="null">Any</option>
            <option :value="15">15 min</option><option :value="30">30 min</option><option :value="45">45 min</option>
            <option :value="60">60 min</option><option :value="90">90 min</option>
          </select>
        </div>
        <div class="sheet__section">
          <span class="sheet__label">Commute limit</span>
          <label class="sheet__check">
            <input v-model="store.showOverCeiling" type="checkbox" />
            Hide houses where any commute is over the family's limit
          </label>
        </div>
        <div class="sheet__section">
          <button class="sheet__apply" @click="showFilterSheet = false">Apply</button>
        </div>
      </div>
    </div>
  </main>

  <!-- Bottom tab bar -->
  <nav class="tab-bar" aria-label="Main navigation">
    <button class="tab-bar__tab" :class="{ 'tab-bar__tab--active': activeTab === 'properties' }" @click="activeTab = 'properties'">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
      </svg>
      <span class="tab-bar__label">Properties</span>
    </button>
    <button class="tab-bar__tab" :class="{ 'tab-bar__tab--active': activeTab === 'favourites' }" @click="activeTab = 'favourites'">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z" />
      </svg>
      <span class="tab-bar__label">Favourites</span>
    </button>
    <button class="tab-bar__tab" :class="{ 'tab-bar__tab--active': activeTab === 'map' }" @click="activeTab = 'map'">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z" />
        <circle cx="12" cy="10" r="3" />
      </svg>
      <span class="tab-bar__label">Map</span>
    </button>
    <button class="tab-bar__add" aria-label="Add a property" @click="openAddSheet">
      <span class="tab-bar__fab">+</span>
      <span class="tab-bar__label tab-bar__label--add">Add</span>
    </button>
  </nav>

  <!-- Add-property bottom sheet (thumb zone, above the tab bar) -->
  <div v-if="addSheetOpen" class="add-sheet-backdrop" @click.self="addSheetOpen = false"></div>
  <section v-if="addSheetOpen" class="add-sheet" aria-label="Add a Rightmove listing">
    <h3 class="add-sheet__title">Add a Rightmove listing</h3>
    <div v-if="addDuplicate" class="add-sheet__duplicate">
      Already added — this house is on your list.
      <button class="btn btn--quiet" @click="jumpToDuplicate">Jump to it</button>
    </div>
    <input
      v-else
      v-model="addUrl"
      class="add-sheet__input"
      :class="{ 'add-sheet__input--error': addError }"
      type="url"
      placeholder="https://www.rightmove.co.uk/properties/…"
      aria-label="Rightmove property URL"
      @keyup.enter="submitAdd"
    >
    <p v-if="addError" class="add-sheet__error">{{ addError }}</p>
    <div class="add-sheet__actions">
      <button class="btn btn--quiet" @click="addSheetOpen = false">Cancel</button>
      <button class="btn btn--primary" :disabled="addBusy" @click="submitAdd">
        {{ addBusy ? 'Adding…' : 'Add property' }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.page { max-width:1200px; margin:0 auto; padding:12px 12px 0; }
.search-section { padding: 12px 0 0; }
.search-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--card-bg);
  border: 1.5px solid var(--border);
  border-radius: var(--radius);
  padding: 11px 14px;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.search-bar:focus-within {
  border-color: var(--blue);
  box-shadow: 0 0 0 3px rgba(45, 106, 79, 0.12);
}
.search-icon { flex-shrink: 0; width: 18px; height: 18px; color: var(--text-muted); }
.search-input { flex: 1; border: none; background: none; font-size: 0.9375rem; color: var(--text); outline: none; }
.search-input::placeholder { color: var(--text-muted); }

.controls-row { display: flex; align-items: center; gap: 8px; padding: 12px 0 0; }
.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border-radius: var(--radius-full);
  font-size: 0.8125rem;
  font-weight: var(--fw-medium);
  border: 1.5px solid var(--border);
  background: var(--card-bg);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
  min-height: 40px;
}
.pill:hover { border-color: var(--text-muted); }
.pill--active { background: var(--blue); color: #fff; border-color: var(--blue); }
.pill-icon { width: 14px; height: 14px; flex-shrink: 0; }
.pill-badge {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px;
  font-size: 0.6875rem; font-weight: var(--fw-semibold); background: var(--blue); color: #fff;
}
.pill--active .pill-badge { background: rgba(255, 255, 255, 0.3); }
.count-text { margin-left: auto; font-size: 0.8125rem; color: var(--text-muted); white-space: nowrap; }

.commute-status {
  display: flex; align-items: center; gap: 8px;
  margin: 10px 0 0; padding: 8px 12px;
  background: var(--amber-bg); border-radius: var(--radius-sm);
  font-size: 0.8125rem; color: var(--amber-text); line-height: 1.35;
}
.commute-status-icon { flex-shrink: 0; font-size: 1rem; }
.commute-status-dismiss {
  margin-left: auto; background: none; border: none; color: var(--amber-text);
  opacity: 0.6; cursor: pointer; padding: 2px; font-size: 1.1rem; line-height: 1; min-width: 32px; min-height: 32px;
}

.legend-strip {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 0 0; flex-wrap: wrap;
}
.legend-item { display: inline-flex; align-items: center; gap: 5px; font-size: 0.6875rem; color: var(--text-muted); }
.legend-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.legend-dot--good { background: var(--green); }
.legend-dot--warn { background: var(--orange); }
.legend-dot--bad { background: var(--red); }
.legend-dot--muted { background: var(--commute-none); }

.tab-heading { font-size:1.1rem; margin:0; padding:8px 0 0; }
.card-list { display:flex; flex-direction:column; gap:12px; padding-top: 14px; }
.empty-state { text-align:center; padding:60px 20px; }
.empty-state__text { font-size: var(--fs-lg); color:var(--text-muted); }
.tab-bar-spacer { height:72px; }
@media (min-width:600px) { .card-list { display:grid; grid-template-columns:1fr 1fr; gap:12px; } .page { padding-left:16px; padding-right:16px; } }
@media (min-width:960px) { .card-list { grid-template-columns:1fr 1fr 1fr; } }

.map-full { position:fixed; top:56px; left:0; right:0; bottom:56px; z-index:1; }
.map-full .mapview-wrap { height: 100%; }

.sheet-overlay { position:fixed; inset:0; background:rgba(0,0,0,0.4); z-index:90; }
.sheet {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: var(--card-bg);
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  z-index: 100;
  padding: var(--sp-3) var(--sp-4) var(--sp-6);
  max-height: 80vh;
  overflow-y: auto;
  box-shadow: 0 -4px 24px rgba(0, 0, 0, 0.08);
}
.sheet__handle {
  width: 40px;
  height: var(--sp-1);
  background: var(--slate-300);
  border-radius: var(--radius-full);
  margin: 0 auto var(--sp-3);
}
.sheet__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--sp-3);
}
.sheet__title {
  font-size: var(--fs-lg);
  font-weight: var(--fw-bold);
  line-height: var(--lh-tight);
  color: var(--text);
}
.sheet__close {
  border: none;
  background: none;
  font-size: var(--fs-lg);
  cursor: pointer;
  color: var(--text-muted);
  min-width: 44px;
  min-height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}
.sheet__body {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.sheet__section {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.sheet__label {
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-muted);
  line-height: var(--lh-tight);
}
.sheet__check {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-sm);
  color: var(--text);
  min-height: 44px;
}
.sheet__check input {
  width: 16px;
  height: 16px;
  accent-color: var(--green);
}
.sheet__select,
.sheet__input {
  font: inherit;
  padding: var(--sp-2) var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: var(--fs-base);
  line-height: var(--lh-tight);
  width: 100%;
  min-height: 44px;
  color: var(--text);
  background: var(--card-bg);
}
.sheet__helper {
  margin: var(--sp-1) 0 0;
  font-size: var(--fs-xs);
  color: var(--text-secondary);
}
.baseline-legend {
  margin: 0 0 var(--sp-3);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}
.sheet__apply {
  width: 100%;
  padding: var(--sp-3);
  border: none;
  border-radius: var(--radius);
  background: var(--green);
  color: #fff;
  font-size: var(--fs-base);
  font-weight: var(--fw-semibold);
  line-height: var(--lh-tight);
  min-height: 44px;
  cursor: pointer;
}

.tab-bar { position:fixed; bottom:0; left:0; right:0; height:56px; background:var(--card-bg); border-top:1px solid var(--border); display:flex; align-items:center; justify-content:space-around; z-index:80; padding-bottom:env(safe-area-inset-bottom,0); }
.tab-bar__tab { display:flex; flex-direction:column; align-items:center; gap:2px; border:none; background:none; cursor:pointer; color:var(--text-muted); min-width:56px; min-height:44px; padding:4px 12px; }
.tab-bar__tab--active { color:var(--blue); }
.tab-bar__tab--active svg { stroke:var(--blue); }
.map-fallback-note {
  position: absolute;
  top: 8px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.4rem 0.8rem;
  font-size: 0.85rem;
  z-index: 2;
}
.tab-bar__label { font-size: var(--fs-xs); font-weight: var(--fw-semibold); }
/* ── Add FAB + bottom sheet ─────────────────────────────────────────── */
.tab-bar__add { display: flex; flex-direction: column; align-items: center; gap: 2px; border: none; background: none; min-width: 64px; min-height: 52px; cursor: pointer; }
.tab-bar__fab { width: 48px; height: 48px; border-radius: 50%; background: var(--green); color: #fff; font-size: 26px; font-weight: 700; display: grid; place-items: center; margin-top: -18px; box-shadow: 0 4px 10px rgba(45, 106, 79, 0.4); }
.tab-bar__label--add { color: var(--green-text); font-weight: 600; }
.add-sheet-backdrop { position: fixed; inset: 0; background: rgba(26, 26, 26, 0.3); z-index: 40; }
.add-sheet { position: fixed; left: 8px; right: 8px; bottom: 74px; background: var(--card-bg); border-radius: var(--radius-lg); box-shadow: var(--shadow); padding: 16px; z-index: 41; display: flex; flex-direction: column; gap: 10px; }
.add-sheet__title { font-size: var(--fs-base); font-weight: 700; }
.add-sheet__input { width: 100%; min-height: 48px; border: 1px solid var(--slate-300); border-radius: var(--radius-sm); padding: 0 12px; font-size: var(--fs-base); }
.add-sheet__input--error { border-color: var(--red); }
.add-sheet__error { color: var(--red-text); font-size: var(--fs-sm); }
.add-sheet__duplicate { display: flex; align-items: center; justify-content: space-between; gap: 8px; background: var(--amber-bg); color: var(--amber-text); border-radius: var(--radius-sm); padding: 10px 12px; font-size: var(--fs-sm); font-weight: 600; }
.add-sheet__actions { display: flex; gap: 8px; }
.add-sheet__actions .btn { flex: 1; min-height: 48px; border: none; border-radius: var(--radius-sm); font-size: var(--fs-sm); font-weight: 600; cursor: pointer; }
.add-sheet__actions .btn--primary { background: var(--green); color: #fff; }
.add-sheet__actions .btn--primary:disabled { opacity: 0.5; }
.add-sheet__actions .btn--quiet { background: var(--slate-100); color: var(--slate-700); }
</style>
