<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePropertiesStore } from '../stores/properties'
import Header from '../components/Header.vue'
import CommuteSection from '../components/CommuteSection.vue'
import CostsSection from '../components/CostsSection.vue'
import SchoolsSection from '../components/SchoolsSection.vue'
import NotesSection from '../components/NotesSection.vue'

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

// ── Existing computed / helpers ──────────────────────
const address = computed(() => detail.value?.best_address?.value ?? rid.value)

const price = computed(() => detail.value?.rightmove_price?.succeeded
  ? detail.value.rightmove_price.value?.amount ?? null : null)

const bedrooms = computed(() => detail.value?.rightmove_bedrooms?.succeeded
  ? detail.value.rightmove_bedrooms.value : null)

const monthlyCost = computed(() => detail.value?.affordability?.total_monthly_housing_cost?.succeeded
  ? detail.value.affordability.total_monthly_housing_cost.value?.amount ?? null : null)

// ── Surface existing data ────────────────────────────
const townDescription = computed(() => {
  const td = detail.value?.area?.town_description
  if (!td?.succeeded || !td.value) return null
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

// ── Share / Favourite ────────────────────────────────
async function shareProperty() {
  const url = rightmoveUrl.value || window.location.href
  if (navigator.share) {
    await navigator.share({ title: address.value, url })
  }
}

async function toggleFavourite() {
  await store.toggleTriage(rid.value, 'favourite', !triage.value?.favourite)
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
          <span v-if="price" class="summary-price">£{{ price.toLocaleString() }}</span>
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
            {{ (walkability as Record<string, unknown>).walk_to_town_minutes ?? '?' }} min to {{ detail?.town_name?.value ?? 'town' }}
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
      <CommuteSection
        :commutes="detail.commutes"
        :good-threshold="store.settings.commute_thresholds?.good ?? 45"
        :warn-threshold="store.settings.commute_thresholds?.warn ?? 75"
      />

      <!-- ═══════════ SCHOOLS ═══════════ -->
      <SchoolsSection
        :schools="detail.schools"
        :commutes="detail.commutes"
      />

      <!-- ═══════════ COSTS ═══════════ -->
      <CostsSection
        :affordability="detail.affordability"
        :epc="detail.epc"
      />

      <!-- ═══════════ NOTES ═══════════ -->
      <NotesSection
        :rid="rid"
        :triage="triage"
        :comments="detail.comments"
      />
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

/* Detail sections — only summary-specific styles */
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
