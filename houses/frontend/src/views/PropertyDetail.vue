<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { usePropertiesStore } from '../stores/properties'
import Header from '../components/Header.vue'
import * as api from '../services/api'
import CommuteSection from '../components/CommuteSection.vue'
import CostsSection from '../components/CostsSection.vue'
import SchoolsSection from '../components/SchoolsSection.vue'
import NotesSection from '../components/NotesSection.vue'
const route = useRoute()
const router = useRouter()
const store = usePropertiesStore()
const auth = useAuthStore()

const currentPerson = computed(() => auth.user?.person ?? null)

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
  ? parseFloat(detail.value.rightmove_price.value?.amount ?? '0') || null : null)

const bedrooms = computed(() => detail.value?.rightmove_bedrooms?.succeeded
  ? detail.value.rightmove_bedrooms.value : null)

const monthlyCost = computed(() => {
  const g = detail.value?.affordability?.group_monthly_cost
  if (!g?.succeeded || !g.value?.couple) return null
  return Number(g.value.couple.value)
})

// Part A: an approximate total (stddev > 0) renders as "≈ £X/mo".
const monthlyCostApprox = computed(() => {
  const g = detail.value?.affordability?.group_monthly_cost
  return !!g?.succeeded && ((g.value?.couple?.stddev ?? 0) > 0)
})

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

// ── Address correction (C2) ─────────────────────────
// A missing Council Tax lookup usually means the address isn't exact
// enough.  The user corrects the address here; saving refetches the
// detail so the DAG recomputes council tax (and everything downstream).
const editingAddress = ref(false)
const addressDraft = ref('')
const addressSaving = ref(false)
const addressError = ref('')
const addressSaved = ref(false)

function startAddressEdit() {
  addressDraft.value = address.value
  addressError.value = ''
  addressSaved.value = false
  editingAddress.value = true
}

function cancelAddressEdit() {
  editingAddress.value = false
  addressError.value = ''
}

async function saveAddress() {
  const draft = addressDraft.value.trim()
  if (!draft) {
    addressError.value = "Address can't be empty."
    return
  }
  addressSaving.value = true
  addressError.value = ''
  try {
    await api.patchAddress(rid.value, draft)
    editingAddress.value = false
    addressSaved.value = true
    // refetch: the DAG recomputes council tax and every downstream total
    await store.loadDetail(rid.value)
    setTimeout(() => (addressSaved.value = false), 3000)
  } catch {
    addressError.value = 'Could not save the address — try again.'
  } finally {
    addressSaving.value = false
  }
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
        <div class="summary-address-row">
          <h1 class="summary-address">{{ address }}</h1>
          <button v-if="!editingAddress" class="summary-address-edit" @click="startAddressEdit">Edit address</button>
          <span v-if="addressSaved" class="summary-address-saved">Saved — updating…</span>
        </div>
        <div v-if="editingAddress" class="address-editor">
          <input
            v-model="addressDraft"
            class="address-edit-input"
            aria-label="Correct the property address"
          />
          <button class="address-edit-save" :disabled="addressSaving" @click="saveAddress">
            {{ addressSaving ? 'Saving…' : 'Save' }}
          </button>
          <button class="address-edit-cancel" @click="cancelAddressEdit">Cancel</button>
          <p v-if="addressError" class="address-edit-error">{{ addressError }}</p>
        </div>
        <div class="summary-row">
          <span v-if="price" class="summary-price">£{{ price.toLocaleString() }}</span>
          <span
            v-if="monthlyCost !== null"
            class="summary-monthly"
            :title="monthlyCostApprox ? 'Council tax estimated — total is approximate' : undefined"
          >{{ monthlyCostApprox ? '≈' : '' }}£{{ monthlyCost.toLocaleString() }}/mo</span>
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
            {{ ((walkability as Record<string, unknown>).walk_to_town as any)?.value ?? '?' }} min to {{ detail?.town_name?.value ?? 'town' }}
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
        :current-person="currentPerson"
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
        :persons="detail.settings?.persons"
        :rid="rid"
        :current-person="currentPerson"
      />

      <!-- ═══════════ NOTES ═══════════ -->
      <NotesSection :rid="rid" />
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
  max-width: var(--content-max-w);
  margin: 0 auto;
  padding: 0 0 80px;
}
.empty-state { text-align: center; padding: var(--sp-10) var(--sp-5); }
.empty-state__text { font-size: var(--fs-base); color: var(--text-muted); }
.btn--primary {
  display: inline-block;
  padding: 0.6em 1.2em;
  font-size: var(--fs-sm);
  border-radius: var(--radius);
  border: none;
  background: var(--blue);
  color: #fff;
  cursor: pointer;
  text-decoration: none;
  font-weight: var(--fw-semibold);
  transition: background var(--transition);
}
.btn--primary:hover { background: var(--blue-text); }
.btn--secondary {
  display: inline-block;
  padding: 0.6em 1.2em;
  font-size: var(--fs-sm);
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--card-bg);
  color: var(--text);
  cursor: pointer;
  text-decoration: none;
  font-weight: var(--fw-medium);
  transition: all var(--transition);
}
.btn--secondary:hover { background: var(--slate-50); border-color: var(--slate-300); }

/* Summary bar */
.summary-bar {
  position: sticky;
  top: 0;
  z-index: 20;
  background: var(--card-bg);
  padding: var(--sp-4) var(--sp-6);
  border-bottom: 1px solid var(--border);
}
.summary-address-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
}
.summary-address-edit {
  color: var(--blue);
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.85rem;
  padding: 0;
}
.summary-address-saved {
  color: var(--green);
  font-size: 0.85rem;
}
.address-editor {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 0.4rem;
}
.address-edit-input {
  flex: 1;
  min-width: 14rem;
  padding: 0.35rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.address-edit-save {
  background: var(--blue);
  color: white;
  border: none;
  border-radius: 6px;
  padding: 0.35rem 1rem;
  cursor: pointer;
}
.address-edit-cancel {
  background: none;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.35rem 0.8rem;
  cursor: pointer;
}
.address-edit-error {
  color: var(--red);
  font-size: 0.85rem;
  width: 100%;
  margin: 0.25rem 0 0;
}
.summary-address {
  font-size: var(--fs-xl);
  font-weight: var(--fw-bold);
  margin: 0 0 var(--sp-2);
  color: var(--slate-900);
  line-height: var(--lh-tight);
}
.summary-row {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
}
.summary-price { font-size: var(--fs-base); font-weight: var(--fw-bold); color: var(--slate-800); }
.summary-monthly { font-size: var(--fs-sm); font-weight: var(--fw-semibold); color: var(--green); margin-left: auto; }
.summary-bedrooms { font-size: var(--fs-sm); color: var(--text-secondary); }

/* Section nav */
.section-nav-wrap {
  position: sticky;
  top: 0;
  z-index: 19;
  background: var(--card-bg);
  border-bottom: 1px solid var(--border);
  overflow-x: auto;
}
.section-nav {
  display: flex;
  max-width: var(--content-max-w);
  margin: 0 auto;
}
.section-nav__tab {
  flex: 1;
  min-width: 0;
  padding: var(--sp-3) var(--sp-2);
  border: none;
  background: none;
  cursor: pointer;
  font-size: var(--fs-sm);
  font-weight: var(--fw-medium);
  color: var(--text-secondary);
  white-space: nowrap;
  text-align: center;
  border-bottom: 2px solid transparent;
  transition: all var(--transition);
}
.section-nav__tab:hover { color: var(--slate-800); }
.section-nav__tab--active {
  color: var(--slate-900);
  font-weight: var(--fw-bold);
  border-bottom-color: var(--slate-900);
}

/* Icon buttons in header */
.btn--icon {
  width: 44px;
  height: 44px;
  border-radius: var(--radius-full);
  background: rgba(255,255,255,0.12);
  color: #fff;
  font-size: var(--fs-xl);
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0.7;
  border: none;
  cursor: pointer;
  transition: opacity var(--transition), background var(--transition);
}
.btn--icon:hover { opacity: 1; background: rgba(255,255,255,0.2); }
.btn--icon--active { color: var(--amber); opacity: 1; }

/* Detail sections */
.detail-section {
  padding: var(--sp-5) var(--sp-6);
  border-bottom: 8px solid var(--slate-50);
}
.detail-section__title {
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  color: var(--slate-800);
  margin: 0 0 var(--sp-4);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* Detail fields */
.detail-field {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--sp-2);
  padding: var(--sp-2) 0;
}
.detail-field--block { flex-direction: column; align-items: stretch; }
.detail-field__label { font-size: var(--fs-sm); font-weight: var(--fw-semibold); color: var(--slate-500); min-width: 100px; }
.detail-field__value { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; font-size: var(--fs-sm); }
.detail-field__value a { color: var(--blue); text-decoration: none; }
.detail-field__value a:hover { text-decoration: underline; }

/* Map */
.map-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 160px;
  background: var(--slate-200);
  border-radius: var(--radius-lg);
  color: var(--slate-400);
  gap: var(--sp-2);
  margin-bottom: var(--sp-3);
}
.map-placeholder__text { font-size: var(--fs-sm); }

.detail-town-desc { font-size: var(--fs-sm); line-height: var(--lh); color: var(--text-secondary); margin: 0; }

.detail-actions { display: flex; gap: var(--sp-2); margin-top: var(--sp-3); flex-wrap: wrap; }

.notes-textarea {
  width: 100%;
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--card-bg);
  color: var(--text);
  resize: vertical;
  font-family: inherit;
  font-size: var(--fs-sm);
  box-sizing: border-box;
}
.notes-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 6px;
}

/* Tab bar */
.tab-bar {
  position: fixed;
  bottom: 0; left: 0; right: 0;
  height: var(--tabbar-h);
  background: var(--card-bg);
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-around;
  z-index: 80;
  padding-bottom: env(safe-area-inset-bottom, 0);
}
.tab-bar__tab {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  border: none;
  background: none;
  cursor: pointer;
  color: var(--text-muted);
  min-width: 56px;
  min-height: 44px;
  padding: var(--sp-1) var(--sp-3);
}
.tab-bar__tab--active { color: var(--blue); }


.tab-bar__tab--active svg { stroke: var(--blue); }
.tab-bar__label { font-size: var(--fs-xs); font-weight: var(--fw-semibold); }

@media (max-width: 767px) {
  .summary-bar { padding: var(--sp-3) var(--sp-4); }
  .detail-section { padding: var(--sp-4); }
  .section-nav__tab { padding: var(--sp-3); font-size: var(--fs-xs); }
}
</style>
