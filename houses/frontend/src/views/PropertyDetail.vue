<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePropertiesStore } from '../stores/properties'
import { patchAddress, patchLocation } from '../services/api'
import Header from '../components/Header.vue'
const route = useRoute()
const router = useRouter()
const store = usePropertiesStore()

const rid = computed(() => route.params.rid as string)
const detail = computed(() => store.details[rid.value])

const editingAddress = ref('')
const editingLat = ref('')
const editingLon = ref('')
watch(() => route.params.rid, (newRid) => {
  if (newRid) store.loadDetail(newRid as string)
}, { immediate: true })


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

function ofstedClass(rating: string | null): string {
  if (rating === 'Outstanding') return 'pill--good'
  if (rating === 'Good') return 'pill--warn'
  if (rating === 'Requires Improvement') return 'pill--bad'
  return 'pill--muted'
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
  if (mins < 45) return 'pill--good'
  if (mins <= 75) return 'pill--warn'
  return 'pill--bad'
}

async function saveAddress() {
  if (editingAddress.value) {
    await patchAddress(rid.value, editingAddress.value)
  }
  editingAddress.value = ''
}

async function saveLocation() {
  const lat = parseFloat(editingLat.value)
  const lon = parseFloat(editingLon.value)
  if (!isNaN(lat) && !isNaN(lon)) {
    await patchLocation(rid.value, lat, lon)
  }
  editingLat.value = ''
  editingLon.value = ''
}
</script>

<template>
  <Header title="Property Detail">
    <template #actions>
      <button class="btn--icon" @click="router.push('/')">←</button>
    </template>
  </Header>
  <div class="page">
    <div v-if="store.loading && !detail" class="empty-state">
      <p class="empty-state__text">Loading property...</p>
    </div>
    <div v-else-if="!detail" class="empty-state">
      <p class="empty-state__text">Property not found.</p>
      <button class="btn--primary" @click="router.push('/')">Back to list</button>
    </div>
    <template v-else>
      <div class="detail__summary-bar">
        <span class="detail__price">
          {{ detail.best_address.value ?? rid }}
        </span>
        <span v-if="detail.rightmove_price.succeeded" class="detail__price-value">
          £{{ Number(detail.rightmove_price.value).toLocaleString() }}
        </span>
        <span v-if="detail.rightmove_bedrooms.succeeded" class="detail__bedrooms">
          {{ detail.rightmove_bedrooms.value }} bed
        </span>
        <span v-if="detail.affordability.total_monthly_housing_cost.succeeded" class="detail__monthly">
          £{{ detail.affordability.total_monthly_housing_cost.value }}/mo
        </span>
      </div>

      <!-- Commutes -->
      <section class="detail__section">
        <div class="detail__section-header">🚆 Commutes</div>
        <div v-for="(c, key) in detail.commutes" :key="key" class="detail__commute-row">
          <div class="detail__commute-label">{{ key }}</div>
          <div class="detail__commute-value">
            <span class="pill" :class="pillColour(c)">
              {{ commuteDisplay(c)?.duration ?? '?' }}
              {{ commuteDisplay(c)?.cost ?? '' }}
            </span>
          </div>
          <div class="detail__provenance">
            {{ c.provenance?.label ?? 'unknown' }}
          </div>
        </div>
      </section>

      <!-- Location -->
      <section class="detail__section">
        <div class="detail__section-header">📍 Location</div>
        <div class="detail__field">
          <div class="detail__field-label">Address</div>
          <div class="detail__field-value">
            <span v-if="!editingAddress">{{ detail.best_address.value ?? 'Unknown' }}</span>
            <input v-else v-model="editingAddress" class="detail__edit-input" />
            <button v-if="!editingAddress" class="btn--small" @click="editingAddress = detail.best_address.value ?? ''">✏️</button>
            <button v-else class="btn--small btn--save" @click="saveAddress()">Save</button>
          </div>
        </div>
        <div v-if="detail.location.best_location.succeeded" class="detail__field">
          <div class="detail__field-label">Coordinates</div>
          <div class="detail__field-value">
            <template v-if="!editingLat">
              {{ detail.location.best_location.value?.lat.toFixed(4) }}, {{ detail.location.best_location.value?.lon.toFixed(4) }}
              <button class="btn--small" @click="editingLat = String(detail.location.best_location.value?.lat); editingLon = String(detail.location.best_location.value?.lon)">✏️</button>
            </template>
            <template v-else>
              <input v-model="editingLat" class="detail__edit-input detail__edit-input--short" />,
              <input v-model="editingLon" class="detail__edit-input detail__edit-input--short" />
              <button class="btn--small btn--save" @click="saveLocation()">Save</button>
            </template>
          </div>
        </div>
      </section>

      <!-- Schools -->
      <section class="detail__section">
        <div class="detail__section-header">🏫 Schools</div>
        <div v-if="detail.schools.primary.school.succeeded" class="detail__field">
          <div class="detail__field-label">Primary</div>
          <div class="detail__field-value">
            <a :href="detail.schools.primary.school.value!.url" target="_blank">{{ detail.schools.primary.school.value!.name }}</a>
            <span class="pill pill--sm" :class="ofstedClass(detail.schools.primary.school.value!.ofsted)">{{ detail.schools.primary.school.value!.ofsted }}</span>
            <span v-if="schoolWalkMin(detail.commutes, 'Primary')" class="pill pill--sm pill--good">{{ schoolWalkMin(detail.commutes, 'Primary') }}m walk</span>
          </div>
        </div>
        <div v-if="detail.schools.secondary.school.succeeded" class="detail__field">
          <div class="detail__field-label">Secondary</div>
          <div class="detail__field-value">
            <a :href="detail.schools.secondary.school.value!.url" target="_blank">{{ detail.schools.secondary.school.value!.name }}</a>
            <span class="pill pill--sm" :class="ofstedClass(detail.schools.secondary.school.value!.ofsted)">{{ detail.schools.secondary.school.value!.ofsted }}</span>
            <span v-if="schoolWalkMin(detail.commutes, 'Secondary')" class="pill pill--sm pill--good">{{ schoolWalkMin(detail.commutes, 'Secondary') }}m walk</span>
          </div>
        </div>
      </section>

      <!-- Affordability -->
      <section class="detail__section">
        <div class="detail__section-header">💰 Affordability</div>
        <div class="detail__field">
          <div class="detail__field-label">Monthly Mortgage</div>
          <div class="detail__field-value">£{{ detail.affordability.monthly_mortgage.value ?? '?' }}</div>
        </div>
        <div class="detail__field">
          <div class="detail__field-label">Monthly Sinking Fund</div>
          <div class="detail__field-value">£{{ detail.affordability.monthly_sinking_fund.value ?? '?' }}</div>
        </div>
        <div class="detail__field">
          <div class="detail__field-label">Monthly Commute Cost</div>
          <div class="detail__field-value">£{{ detail.affordability.monthly_commute_cost.value?.yearly_total_gbp != null ? (detail.affordability.monthly_commute_cost.value.yearly_total_gbp / 12).toFixed(2) : '?' }}</div>
        </div>
        <div class="detail__field">
          <div class="detail__field-label">Council Tax</div>
          <div class="detail__field-value">{{ detail.affordability.council_tax.value?.band ?? '?' }} · £{{ detail.affordability.council_tax.value?.yearly_cost ?? '?' }}/yr</div>
        </div>
        <div class="detail__field" v-if="detail.affordability.stamp_duty">
          <div class="detail__field-label">Stamp Duty</div>
          <div class="detail__field-value">£{{ detail.affordability.stamp_duty.succeeded ? detail.affordability.stamp_duty.value?.toLocaleString() : '?' }}</div>
        </div>
        <div class="detail__field detail__field--total">
          <div class="detail__field-label">Total Monthly</div>
          <div class="detail__field-value">£{{ detail.affordability.total_monthly_housing_cost.value ?? '?' }}</div>
        </div>
      </section>

      <!-- Comments -->
      <section class="detail__section">
        <div class="detail__section-header">📝 Comments</div>
        <div class="detail__field">
          <div class="detail__field-label">Status</div>
          <div class="detail__field-value">{{ detail.comments.status.value ?? '—' }}</div>
        </div>
        <div class="detail__field">
          <div class="detail__field-label">Group Notes</div>
          <div class="detail__field-value">{{ detail.comments.group_notes.value ?? '—' }}</div>
        </div>
        <div class="detail__field">
          <div class="detail__field-label">Design Needed</div>
          <div class="detail__field-value">{{ detail.comments.design_needed.value ?? '—' }}</div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 12px 16px 40px;
}
.btn--icon {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: rgba(255,255,255,0.12);
  color: #fff;
  font-size: 20px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0.5;
  border: none;
  cursor: pointer;
}
.btn--small {
  font-size: 12px;
  padding: 2px 6px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--card-bg);
  cursor: pointer;
  margin-left: 6px;
}
.btn--save {
  background: var(--blue);
  color: #fff;
  border-color: var(--blue);
}
.empty-state {
  text-align: center;
  padding: 60px 20px;
}
.empty-state__text {
  font-size: 16px;
  color: var(--text-muted);
}
.btn--primary {
  display: inline-block;
  padding: 0.6em 1.2em;
  font-size: 0.95em;
  border-radius: 6px;
  border: none;
  background: #1565c0;
  color: #fff;
  cursor: pointer;
  text-decoration: none;
  margin-top: 1em;
}
.detail__summary-bar {
  display: flex;
  gap: 1em;
  align-items: center;
  padding: 1em 0;
  flex-wrap: wrap;
  border-bottom: 1px solid var(--border);
  margin-bottom: 1em;
}
.detail__price { font-weight: 700; }
.detail__price-value { font-weight: 600; color: var(--green); }
.detail__bedrooms { color: var(--text-secondary); font-size: 0.9em; }
.detail__monthly { margin-left: auto; font-weight: 600; color: var(--blue); }
.detail__section {
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 1em;
  overflow: hidden;
}
.detail__section-header {
  padding: 0.75em 1em;
  background: #f9f9f9;
  font-weight: 600;
  font-size: 0.95em;
  border-bottom: 1px solid var(--border);
}
.detail__field {
  padding: 0.75em 1em;
  border-bottom: 1px solid #f0f0f0;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.5em;
}
.detail__field:last-child { border-bottom: none; }
.detail__field--total {
  background: #f0f7ff;
  font-weight: 600;
}
.detail__field-label {
  font-size: 0.85em;
  color: #888;
  min-width: 120px;
}
.detail__field-value {
  font-size: 1em;
  display: flex;
  align-items: center;
  gap: 0.5em;
  flex-wrap: wrap;
}
.detail__edit-input {
  font: inherit;
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: 4px;
  width: 300px;
}
.detail__edit-input--short { width: 100px; }
.detail__provenance {
  font-size: 0.75em;
  color: var(--text-muted);
  margin-left: auto;
}
.detail__commute-row {
  padding: 0.75em 1em;
  border-bottom: 1px solid #f0f0f0;
  display: flex;
  align-items: center;
  gap: 0.75em;
}
.detail__commute-row:last-child { border-bottom: none; }
.detail__commute-label {
  font-weight: 600;
  font-size: 0.9em;
  min-width: 100px;
}
.detail__commute-value { display: flex; gap: 4px; }
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
.pill--good { background: var(--green-bg); color: var(--green); }
.pill--warn { background: var(--orange-bg); color: var(--orange); }
.pill--bad { background: var(--red-bg); color: var(--red); }
.pill--muted { background: var(--muted-bg); color: var(--muted); }
</style>
