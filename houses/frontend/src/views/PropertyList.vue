<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { usePropertiesStore } from '../stores/properties'
import type { PropertySummary } from '../types'
import Header from '../components/Header.vue'
import PropertyCard from '../components/PropertyCard.vue'
import WhatIfPanel from '../components/WhatIfPanel.vue'

const store = usePropertiesStore()

const sortBy = ref<string>('date_added')
const showFilterSheet = ref(false)
const activeTab = ref<'properties' | 'favourites' | 'map'>('properties')
const maxPriceFilter = ref<number | null>(null)
const minBedroomsFilter = ref<number | null>(null)
const maxCommuteFilter = ref<number | null>(null)
const mapFailed = ref(false)

onMounted(() => { store.loadAll() })

const sortOptions = [
  { value: 'price_asc', label: 'Price: Low→High' },
  { value: 'price_desc', label: 'Price: High→Low' },
  { value: 'monthly_cost', label: 'Monthly Cost' },
  { value: 'commute', label: 'Commute Time' },
  { value: 'ofsted', label: 'Ofsted Rating' },
  { value: 'bedrooms', label: 'Bedrooms' },
  { value: 'date_added', label: 'Date Added' },
]

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
  const m = store.monthlyTotalFor(rid)
  if (!m) return Infinity
  return parseFloat(m.value.amount)
}

/** Card data with the hypothetical total overlaid while the what-if is
 *  active (labelled 'what-if' so the card can mark it as a preview). */
function cardData(rid: string): PropertySummary {
  const s = store.summaries[rid]
  const wt = store.whatIfTotals?.[rid]
  if (!s || !wt) return s
  return {
    ...s,
    total_monthly_cost: { succeeded: true, value: wt, error: null, provenance: { label: 'what-if' } },
  }
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

function _mapBounds() {
  const locs = allLocations()
  if (locs.length === 0) return null
  let minLat = 90, maxLat = -90, minLon = 180, maxLon = -180
  for (const l of locs) {
    if (l.lat < minLat) minLat = l.lat; if (l.lat > maxLat) maxLat = l.lat
    if (l.lon < minLon) minLon = l.lon; if (l.lon > maxLon) maxLon = l.lon
  }
  const pad = Math.max((maxLat - minLat) * 0.3, 0.01)
  return { minLat: minLat - pad, maxLat: maxLat + pad, minLon: minLon - pad, maxLon: maxLon + pad }
}

function mapBbox() {
  const b = _mapBounds()
  if (!b) return '-0.2,51.3,0.0,51.7'
  return `${b.minLon.toFixed(4)},${b.minLat.toFixed(4)},${b.maxLon.toFixed(4)},${b.maxLat.toFixed(4)}`
}

function pinStyle(lat: number, lon: number) {
  const b = _mapBounds()
  if (!b) return { display: 'none' }
  const lonRange = b.maxLon - b.minLon
  const latRange = b.maxLat - b.minLat
  if (lonRange === 0 || latRange === 0) return { display: 'none' }
  return {
    left: `${((lon - b.minLon) / lonRange * 100).toFixed(2)}%`,
    top: `${(100 - (lat - b.minLat) / latRange * 100).toFixed(2)}%`,
  }
}

// ── Display list (filtered + sorted) ──────────────────

const displayedRids = computed(() => {
  let rids = store.rids
  if (activeTab.value === 'favourites') {
    rids = rids.filter(rid => store.triage[rid]?.favourite)
  }
  if (maxPriceFilter.value != null) {
    rids = rids.filter(rid => monthlyCostNum(rid) <= maxPriceFilter.value!)
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
    case 'ofsted': rids.sort((a, b) => bestOfsted(a) - bestOfsted(b)); break
    case 'bedrooms': rids.sort((a, b) => bedroomNum(b) - bedroomNum(a)); break
    case 'date_added': rids.sort((a, b) => addedDate(b).localeCompare(addedDate(a))); break
  }
  return rids
})

const activeChips = computed(() => {
  const chips: { label: string; key: string }[] = []
  if (maxPriceFilter.value != null) chips.push({ label: `Max monthly: £${maxPriceFilter.value}/mo`, key: 'maxPrice' })
  if (minBedroomsFilter.value != null) chips.push({ label: `Beds: ${minBedroomsFilter.value}+`, key: 'minBeds' })
  if (maxCommuteFilter.value != null) chips.push({ label: `Commute < ${maxCommuteFilter.value}m`, key: 'maxCommute' })
  if (searchQuery.value.trim()) chips.push({ label: `Search: ${searchQuery.value.trim()}`, key: 'search' })
  if (hiddenOverCeilingCount.value > 0) {
    const n = hiddenOverCeilingCount.value
    const hidesEverything = n >= store.rids.length && store.rids.length > 0
    chips.push(
      store.showOverCeiling
        ? { label: `Hiding ${n} house${n === 1 ? '' : 's'} over the family's commute limit`, key: 'ceiling' }
        : hidesEverything
          ? { label: `All ${n} houses are over the family's commute limit — change the limit in Settings`, key: 'ceiling' }
          : { label: `${n} house${n === 1 ? '' : 's'} over the family's commute limit — tap to hide`, key: 'ceiling' },
    )
  }
  return chips
})
function removeChip(key: string) {
  if (key === 'maxPrice') maxPriceFilter.value = null
  if (key === 'minBeds') minBedroomsFilter.value = null
  if (key === 'maxCommute') maxCommuteFilter.value = null
  if (key === 'search') searchQuery.value = ''
  if (key === 'ceiling') {
    // never toggle into a state where the filter hides EVERY house
    if (store.showOverCeiling || hiddenOverCeilingCount.value < store.rids.length) {
      store.showOverCeiling = !store.showOverCeiling
    }
  }
}
function clearAllFilters() {
  maxPriceFilter.value = null; minBedroomsFilter.value = null; maxCommuteFilter.value = null
  searchQuery.value = ''; store.showOverCeiling = false; sortBy.value = 'date_added'
}
const sortLabel = computed(() => sortOptions.find(o => o.value === sortBy.value)?.label ?? 'Sort')
</script>

<template>
  <Header title="Properties" />

  <!-- Map tab -->
  <div v-if="activeTab === 'map'" class="map-full">
    <iframe
      v-if="!mapFailed"
      :src="'https://www.openstreetmap.org/export/embed.html?bbox=' + mapBbox() + '&layer=mapnik'"
      width="100%" height="100%" style="border:0;" loading="lazy"
      referrerpolicy="no-referrer" title="Properties on OpenStreetMap"
      @error="mapFailed = true"
    ></iframe>
    <p v-if="mapFailed" class="map-fallback-note">
      The map didn't load — your browser may block embedded maps. The pins are listed below.
    </p>
    <div class="map-pins">
      <a
        v-for="loc in allLocations()"
        :key="loc.rid"
        :href="'#/property/' + loc.rid"
        class="map-pin"
        :style="pinStyle(loc.lat, loc.lon)"
      >
        <span class="map-pin__label">{{ loc.price || loc.address }}</span>
      </a>
    </div>
  </div>

  <!-- Properties / Favourites tab -->
  <main v-else class="page" role="main">
    <nav class="filter-bar" aria-label="Filter and sort options">
      <button class="filter-btn" @click="showFilterSheet = true">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="4" y1="6" x2="20" y2="6" /><line x1="8" y1="12" x2="20" y2="12" /><line x1="12" y1="18" x2="20" y2="18" />
        </svg>
        <span class="filter-btn__label">{{ sortLabel }}</span>
      </button>
      <button class="filter-btn" @click="showFilterSheet = true">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z" />
        </svg>
        <span class="filter-btn__label">Filter</span>
      </button>
    </nav>

    <div v-if="activeChips.length > 0" class="filter-chips">
      <div class="chips-scroll">
        <span
          v-for="chip in activeChips"
          :key="chip.key"
          class="chip"
          :class="{ 'chip--actionable': chip.key === 'ceiling' && (store.showOverCeiling || hiddenOverCeilingCount < store.rids.length) }"
          :role="chip.key === 'ceiling' ? 'button' : undefined"
          @click="chip.key === 'ceiling' && removeChip('ceiling')"
        >
          {{ chip.label }}
          <button class="chip__remove" @click="removeChip(chip.key)" aria-label="Remove filter">&times;</button>
        </span>
        <button v-if="activeChips.length" class="chips-clear" @click="clearAllFilters">Clear all</button>
      </div>
    </div>

    <div class="search-bar">
      <input
        v-model="searchQuery"
        class="search-input"
        type="search"
        placeholder="Search by area or address"
        aria-label="Search by area or address"
      />
    </div>
    <div class="pill-legend" role="note" aria-label="Commute time colours">
      <span class="pill-legend__item"><i class="pill-legend__dot pill-legend__dot--good"></i>fine</span>
      <span class="pill-legend__item"><i class="pill-legend__dot pill-legend__dot--warn"></i>getting tight</span>
      <span class="pill-legend__item"><i class="pill-legend__dot pill-legend__dot--bad"></i>too far</span>
      <span class="pill-legend__item"><i class="pill-legend__dot pill-legend__dot--muted"></i>no route</span>
    </div>

    <WhatIfPanel :threshold="maxPriceFilter ?? 1500" />

    <h2 v-if="activeTab === 'favourites'" class="tab-heading">Favourites</h2>
    <div class="results-header" role="status" aria-live="polite">
      <template v-if="activeTab === 'favourites'">
        {{ displayedRids.length }} saved house{{ displayedRids.length === 1 ? '' : 's' }}
      </template>
      <template v-else>{{ displayedRids.length }} properties found</template>
    </div>

    <div v-if="store.loading" class="empty-state"><p class="empty-state__text">Loading...</p></div>
    <div v-else-if="store.error" class="empty-state"><p class="empty-state__text">Error: {{ store.error }}</p></div>
    <div v-else-if="displayedRids.length === 0" class="empty-state">
      <p class="empty-state__text">
        <template v-if="store.showOverCeiling && hiddenOverCeilingCount > 0">
          All houses are hidden by the family's commute limit — tap the "Hiding" chip to show them.
        </template>
        <template v-else>No properties match your criteria.</template>
      </p>
    </div>
    <div v-else class="card-list" role="list">
      <template v-for="rid in displayedRids" :key="rid">
        <PropertyCard :rid :data="cardData(rid)" />
      </template>
    </div>

    <div class="tab-bar-spacer" />

    <div v-if="showFilterSheet" class="sheet-overlay" @click="showFilterSheet = false" />
    <div v-if="showFilterSheet" class="sheet" role="dialog" aria-label="Filter properties" aria-modal="true">
      <div class="sheet__handle" />
      <div class="sheet__header">
        <h2 class="sheet__title">Sort &amp; Filter</h2>
        <button class="sheet__close" @click="showFilterSheet = false" aria-label="Close">&times;</button>
      </div>
      <div class="sheet__body">
        <div class="sheet__section">
          <label class="sheet__label">Sort by</label>
          <select v-model="sortBy" class="sheet__select" @change="showFilterSheet = false">
            <option v-for="opt in sortOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </div>
        <div class="sheet__section">
          <label class="sheet__label">Max monthly cost (£)</label>
          <input v-model.number="maxPriceFilter" type="number" class="sheet__input" placeholder="e.g. 3000" min="0" step="100" />
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
  </nav>
</template>

<style scoped>
.page { max-width:1200px; margin:0 auto; padding:12px 12px 0; }
.filter-bar { display:flex; gap:10px; padding:8px 0 4px; }
.filter-btn { display:inline-flex; align-items:center; gap:6px; min-width:44px; min-height:44px; padding:6px 16px; border-radius:999px; background:var(--card-bg); border:1px solid var(--border); color:var(--text); font-size:14px; font-weight:500; cursor:pointer; box-shadow:var(--shadow); }
.filter-btn:hover { background:#f5f5f5; }
.filter-btn__label { font-size:13px; }
.filter-chips { padding:4px 0; overflow-x:auto; -webkit-overflow-scrolling:touch; }
.chips-scroll { display:flex; gap:8px; align-items:center; white-space:nowrap; }
.chip { display:inline-flex; align-items:center; gap:4px; padding:6px 12px; border-radius:999px; background:var(--blue-bg); color:var(--blue); font-size:13px; font-weight:500; white-space:nowrap; }
.chip--actionable { cursor:pointer; text-decoration:underline; }
.chip__remove { border:none; background:none; color:var(--blue); font-size:16px; line-height:1; cursor:pointer; padding:0 2px; min-width:24px; min-height:24px; display:flex; align-items:center; justify-content:center; }
.chips-clear { border:none; background:none; color:var(--text-secondary); font-size:13px; cursor:pointer; padding:4px 8px; white-space:nowrap; text-decoration:underline; min-height:44px; }
.results-header { font-size:14px; color:var(--text-secondary); padding:8px 0 12px; }
.tab-heading { font-size:1.1rem; margin:0; padding:8px 0 0; }
.card-list { display:flex; flex-direction:column; gap:12px; }
.empty-state { text-align:center; padding:60px 20px; }
.empty-state__text { font-size:16px; color:var(--text-muted); }
.tab-bar-spacer { height:72px; }
@media (min-width:600px) { .card-list { display:grid; grid-template-columns:1fr 1fr; gap:12px; } .page { padding-left:16px; padding-right:16px; } }
@media (min-width:960px) { .card-list { grid-template-columns:1fr 1fr 1fr; } }

.map-full { position:fixed; top:56px; left:0; right:0; bottom:56px; z-index:1; }
.map-pins { position:absolute; inset:0; pointer-events:none; }
.map-pin { position:absolute; pointer-events:auto; text-decoration:none; transform:translate(-50%,-100%); }
.map-pin__label {
  display:block; background:var(--card-bg); color:var(--text); font-size:11px; font-weight:700;
  padding:2px 8px; border-radius:6px; border:2px solid var(--blue); white-space:nowrap;
  box-shadow:0 1px 4px rgba(0,0,0,0.2); line-height:1.4;
}
.map-pin:hover .map-pin__label { border-color:var(--green); background:var(--green-bg); }

.sheet-overlay { position:fixed; inset:0; background:rgba(0,0,0,0.4); z-index:90; }
.sheet { position:fixed; bottom:0; left:0; right:0; background:var(--card-bg); border-radius:16px 16px 0 0; z-index:100; padding:12px 16px 24px; max-height:80vh; overflow-y:auto; box-shadow:0 -2px 12px rgba(0,0,0,0.15); }
.sheet__handle { width:36px; height:4px; border-radius:2px; background:var(--border); margin:0 auto 12px; }
.sheet__header { display:flex; align-items:center; justify-content:space-between; margin-bottom:16px; }
.sheet__title { font-size:18px; font-weight:700; }
.sheet__close { border:none; background:none; font-size:24px; cursor:pointer; color:var(--text-secondary); min-width:44px; min-height:44px; display:flex; align-items:center; justify-content:center; }
.sheet__body { display:flex; flex-direction:column; gap:16px; }
.sheet__section { display:flex; flex-direction:column; gap:8px; }
.sheet__label { font-size:14px; font-weight:600; color:var(--text-secondary); }
.sheet__select, .sheet__input { font:inherit; padding:10px 12px; border:1px solid var(--border); border-radius:8px; font-size:15px; width:100%; min-height:44px; }
.sheet__apply { width:100%; padding:12px; border:none; border-radius:8px; background:var(--blue); color:#fff; font-size:16px; font-weight:600; cursor:pointer; min-height:44px; }

.tab-bar { position:fixed; bottom:0; left:0; right:0; height:56px; background:var(--card-bg); border-top:1px solid var(--border); display:flex; align-items:center; justify-content:space-around; z-index:80; padding-bottom:env(safe-area-inset-bottom,0); }
.tab-bar__tab { display:flex; flex-direction:column; align-items:center; gap:2px; border:none; background:none; cursor:pointer; color:var(--text-muted); min-width:56px; min-height:44px; padding:4px 12px; }
.tab-bar__tab--active { color:var(--blue); }
.tab-bar__tab--active svg { stroke:var(--blue); }
.search-bar {
  padding: 4px 0 8px;
}
.search-input {
  width: 100%;
  padding: 0.55rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 0.9rem;
}
.pill-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem 1rem;
  padding: 0 0 8px;
  font-size: 0.75rem;
  color: var(--text-muted);
}
.pill-legend__item {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}
.pill-legend__dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
}
.pill-legend__dot--good { background: var(--green); }
.pill-legend__dot--warn { background: var(--orange); }
.pill-legend__dot--bad { background: var(--red); }
.pill-legend__dot--muted { background: var(--slate-100); border: 1px solid var(--border); }
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
.tab-bar__label { font-size:10px; font-weight:600; }
</style>
