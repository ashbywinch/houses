<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import Header from '../components/Header.vue'
import ProvenanceToggle from '../components/ProvenanceToggle.vue'
import * as api from '../services/api'
import { blockPenceKey, integerPounds, normalizePence } from '../formatters/money'
import WholePoundsField from '../components/WholePoundsField.vue'
import type { Provenance } from '../types'

interface PoiSettings {
  label: string
  address: string
  trips_per_week: number
  weeks_per_year: number
  acceptable_modes: string[]
}

interface MoneyValue {
  amount: string
  currency: string
}

interface PersonSettings {
  name: string
  has_car: boolean
  is_child: boolean
  email: string
  is_superuser: boolean
  editable_by: string[]
  editable_by_me: boolean
  selling_home: boolean
  places_of_interest: PoiSettings[]
  home_sale_price?: MoneyValue
  outstanding_mortgage?: MoneyValue
  cash_contribution?: MoneyValue
  life_insurance_monthly?: MoneyValue
  petrol_mpg?: number
  bus_walk_penalty?: { value: number; unit: string }
}

interface Thresholds {
  good_max_minutes: number
  fine_max_minutes: number
}

interface FinancialSettings {
  mortgage_rate: number
  mortgage_term_years: number
  sinking_fund_rate: number
  petrol_mpg: number
  petrol_cost_per_litre: number
}

interface HouseholdDeposit {
  total: MoneyValue
  persons: Record<string, MoneyValue>
  provenance?: Provenance
}

const route = useRoute()
const auth = useAuthStore()

/** Settings are split into two tabs: Finances (default, left) and
 *  Commutes. The deposit and the money fields are read/edited far less
 *  often than destinations — but the deposit frames the money, so it
 *  leads the Finances tab. */
const activeTab = ref<'finances' | 'commutes'>('finances')

/** The settings page shows YOUR settings: the person you're acting as.
 *  Resolution: impersonated person (superuser mode) → the session's
 *  linked person (server matches by email) → the session's display
 *  name. The DAG keys people BY NAME, so the Google/device profile
 *  name is the identity when the email isn't linked yet. */
const me = computed(() => {
  if (auth.superuserMode && auth.impersonating) return auth.impersonating
  const user = auth.user
  if (user?.person) return user.person
  const byName = user?.name ? persons.value.find(p => p.name === user.name) : undefined
  return byName?.name ?? ''
})
const visiblePersons = computed(() => {
  if (!me.value) return persons.value // unlinked session — show everyone read-only
  return persons.value.filter(p => p.name === me.value)
})
const loading = ref(true)
const error = ref('')
const persons = ref<PersonSettings[]>([])
const thresholds = ref<Record<string, Thresholds>>({})
const deposit = ref<HouseholdDeposit | null>(null)
const financial = ref<FinancialSettings | null>(null)

// Display-form copies of the finance fields (rates shown as
// percentages); converted back to stored fractions on save.
const fin = ref<Record<string, string>>({})
function loadFinancial(f: FinancialSettings | null) {
  financial.value = f
  if (!f) return
  fin.value = {
    'mortgage-rate': String(Number((f.mortgage_rate * 100).toFixed(2))),
    'mortgage-term': String(f.mortgage_term_years),
    'sinking-fund': String(Number((f.sinking_fund_rate * 100).toFixed(2))),
    'petrol-cost': String(f.petrol_cost_per_litre),
  }
}

// ── Autosave (C2/C3) ───────────────────────────────────────────
// No Save button: edits persist automatically (debounced + on blur),
// with an explicit status line and Undo. The server PATCH merges, so
// Undo is just the inverse PATCH of the last-saved snapshot.
type SaveState = 'idle' | 'saving' | 'saved' | 'error'
const saveState = ref<Record<string, SaveState>>({})
const undoSnap = ref<Record<string, Record<string, unknown> | null>>({})
const saveTimers: Record<string, ReturnType<typeof setTimeout>> = {}
let financialTimer: ReturnType<typeof setTimeout> | null = null

onBeforeUnmount(() => {
  for (const t of Object.values(saveTimers)) clearTimeout(t)
  if (financialTimer) clearTimeout(financialTimer)
})

function scheduleSave(person: PersonSettings) {
  if (!isOwn(person)) return
  if (saveTimers[person.name]) clearTimeout(saveTimers[person.name])
  saveTimers[person.name] = setTimeout(() => save(person), 800)
}

function flushSave(person: PersonSettings) {
  if (saveTimers[person.name]) {
    clearTimeout(saveTimers[person.name])
    delete saveTimers[person.name]
  }
  if (!isOwn(person)) return
  void save(person)
}

function buildSaveBody(person: PersonSettings): Record<string, unknown> {
  const body: Record<string, unknown> = {
    name: person.name,
    has_car: person.has_car,
    is_child: person.is_child,
    email: person.email,
    is_superuser: person.is_superuser,
    selling_home: person.selling_home,
    places_of_interest: person.places_of_interest,
  }
  for (const f of ['home_sale_price', 'outstanding_mortgage', 'cash_contribution', 'life_insurance_monthly'] as const) {
    if (!person[f]) continue
    // a cleared input serializes as {amount: ''} — normalize to 0 so a
    // momentarily-empty field can't fail the whole save (the server
    // rejects malformed money shapes)
    body[f] = person[f].amount === '' ? { amount: '0', currency: person[f].currency } : person[f]
  }
  if (person.petrol_mpg != null) body.petrol_mpg = person.petrol_mpg
  if (person.bus_walk_penalty) body.bus_walk_penalty = { ...person.bus_walk_penalty }
  const t = thresholds.value[person.name]
  if (t) body.thresholds = { ...t }
  return body
}

async function save(person: PersonSettings) {
  const body = buildSaveBody(person)
  saveState.value[person.name] = 'saving'
  try {
    await api.patchPerson(person.name, body)
    undoSnap.value[person.name] = body
    saveState.value[person.name] = 'saved'
    const name = person.name
    setTimeout(() => {
      if (saveState.value[name] === 'saved') saveState.value[name] = 'idle'
    }, 4000)
  } catch {
    saveState.value[person.name] = 'error'
  }
}

async function undo(person: PersonSettings) {
  const snap = undoSnap.value[person.name]
  if (!snap) return
  saveState.value[person.name] = 'saving'
  try {
    await api.patchPerson(person.name, snap)
    saveState.value[person.name] = 'saved'
  } catch {
    saveState.value[person.name] = 'error'
  }
}

function retry(person: PersonSettings) {
  void save(person)
}

// ── Household finances (shared assumptions) ─────────────
function scheduleFinancialSave() {
  if (financialTimer) clearTimeout(financialTimer)
  financialTimer = setTimeout(() => void saveFinancial(), 800)
}

function flushFinancialSave() {
  if (financialTimer) {
    clearTimeout(financialTimer)
    financialTimer = null
  }
  void saveFinancial()
}

async function saveFinancial() {
  if (!financial.value) return
  const n = (k: string) => Number(fin.value[k] ?? 0)
  await api.patchFinancial({
    mortgage_rate: n('mortgage-rate') / 100,
    mortgage_term_years: Math.round(n('mortgage-term')),
    sinking_fund_rate: n('sinking-fund') / 100,
    petrol_cost_per_litre: n('petrol-cost'),
  })
}

// person-scroll target from the URL (?person=Simon), set by the
// "Change destinations" link on the property page
const targetPerson = computed(() => (route.query.person as string) || '')

// The three ways a commute can happen. "Car" is only offered to people
// who have one; "walk" is always an option (walking is accepted even when
// they also drive — the map draws it only when they don't).
const MODE_OPTIONS = [
  { value: 'transit', label: 'Transit' },
  { value: 'car', label: 'Driving' },
  { value: 'walk', label: 'Walking' },
]

onMounted(load)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = (await api.fetchSettings()) as {
      persons?: { value?: PersonSettings[] }
      commute_thresholds?: { value?: Record<string, Thresholds> }
      household_deposit?: HouseholdDeposit
      financial?: { value?: FinancialSettings }
    }
    persons.value = data.persons?.value ?? []
    thresholds.value = data.commute_thresholds?.value ?? {}
    deposit.value = data.household_deposit ?? null
    loadFinancial(data.financial?.value ?? null)
  } catch {
    error.value = 'Could not load the family settings.'
  } finally {
    loading.value = false
  }
  if (targetPerson.value) {
    await nextTick()
    const el = document.getElementById('person-' + encodeURIComponent(targetPerson.value))
    el?.scrollIntoView?.({ behavior: 'smooth' })
  }
}

function toggleMode(poi: PoiSettings, mode: string) {
  const set = new Set(poi.acceptable_modes)
  if (set.has(mode) && set.size === 1) return  // keep at least one mode —
  // an empty set would be reinterpreted by the migration rule on the server
  if (set.has(mode)) set.delete(mode)
  else set.add(mode)
  poi.acceptable_modes = MODE_OPTIONS.map(o => o.value).filter(m => set.has(m))
}

function moneyInput(money: MoneyValue | undefined, event: Event) {
  // Generic money input — pence allowed (life insurance etc.).
  if (money) money.amount = String((event.target as HTMLInputElement).value)
}

/** Pence-allowed money input: cap at 2dp on blur (GOV.UK rule). */
function penceInput(money: MoneyValue | undefined, event: Event) {
  moneyInput(money, event)
  const el = event.target as HTMLInputElement
  el.value = normalizePence(el.value)
}

function walkMinutes(person: PersonSettings): number {
  return person.bus_walk_penalty?.value ?? 20
}

function setWalkMinutes(person: PersonSettings, e: Event) {
  const v = Number((e.target as HTMLInputElement).value)
  const value = Number.isFinite(v) && v >= 0 ? v : 0
  if (person.bus_walk_penalty) person.bus_walk_penalty.value = value
}

function allCommutesAreSchool(person: PersonSettings): boolean {
  return person.places_of_interest.length > 0 && person.places_of_interest.every(p => p.address === '')
}

function isOwn(person: PersonSettings): boolean {
  return person.editable_by_me === true
}

const anyEditable = computed(() => persons.value.some(isOwn))

function addDestination(person: PersonSettings) {
  // explicit, concrete modes from the start — an empty list would be
  // treated as legacy-unset by the server migration and could route the
  // destination by modes the UI never offered (e.g. car for a no-car
  // person)
  const defaultModes = person.has_car ? ['transit', 'car', 'walk'] : ['transit', 'walk']
  person.places_of_interest.push({
    label: '',
    address: '',
    trips_per_week: 1,
    weeks_per_year: 46,
    acceptable_modes: defaultModes,
  })
}

function removeDestination(person: PersonSettings, index: number) {
  person.places_of_interest.splice(index, 1)
}

function pounds(money: MoneyValue | undefined): string {
  if (!money) return '£0'
  const n = Number(money.amount)
  return '£' + (Number.isFinite(n) ? n.toLocaleString() : money.amount)
}

const depositRows = computed(() => {
  const d = deposit.value
  if (!d) return []
  return persons.value.map(p => ({
    name: p.name,
    amount: d.persons[p.name],
  }))
})
</script>

<template>
  <div class="settings">
    <Header title="Family settings">
      <template #actions>
        <router-link class="settings__back" to="/">← Properties</router-link>
      </template>
    </Header>

    <main class="settings__main">
      <p v-if="loading" class="settings__status">Loading…</p>
      <p v-else-if="error" class="settings__error">{{ error }}</p>
      <p v-else-if="persons.length === 0" class="settings__status">No family members configured.</p>

      <template v-else>
        <p class="settings__intro">
          These are the commutes and finances you'd accept from a new house — every property on the
          list is scored against them.
        </p>

        <!-- Person strip: identity + autosave status, visible on both tabs -->
        <header
          v-for="person in visiblePersons"
          :key="person.name"
          :id="'person-' + encodeURIComponent(person.name)"
          class="settings-person__header settings-person__strip"
          :class="{ 'settings-person--target': targetPerson === person.name }"
        >
          <h2 class="settings-person__name">{{ person.name }}</h2>
          <span v-if="isOwn(person)" class="settings-person__badge">you</span>
          <span v-if="person.is_child" class="settings-person__badge settings-person__badge--child">child</span>
          <span v-if="!isOwn(person)" class="settings-person__locked-note">read-only</span>
          <span v-if="isOwn(person) && saveState[person.name] === 'saving'" class="settings-person__status">Saving…</span>
          <span
            v-else-if="isOwn(person) && saveState[person.name] === 'saved'"
            class="settings-person__status settings-person__status--saved"
          >Saved ✓
            <button class="settings-person__undo" type="button" @mousedown.prevent @click="undo(person)">Undo</button>
          </span>
          <span
            v-else-if="isOwn(person) && saveState[person.name] === 'error'"
            class="settings-person__status settings-person__status--error"
          >Couldn't save —
            <button class="settings-person__undo" type="button" @mousedown.prevent @click="retry(person)">Retry</button>
          </span>
        </header>

        <!-- Tabs: Finances (default, left) | Commutes -->
        <nav class="settings-tabs" role="tablist" aria-label="Settings sections">
          <button
            role="tab"
            type="button"
            :aria-selected="activeTab === 'finances'"
            :class="{ 'settings-tab--active': activeTab === 'finances' }"
            @click="activeTab = 'finances'"
          >Finances</button>
          <button
            role="tab"
            type="button"
            :aria-selected="activeTab === 'commutes'"
            :class="{ 'settings-tab--active': activeTab === 'commutes' }"
            @click="activeTab = 'commutes'"
          >Commutes</button>
        </nav>

        <!-- ═══════ FINANCES ═══════ -->
        <div v-if="activeTab === 'finances'" class="settings-panel" role="tabpanel">
          <div v-if="deposit" class="settings-deposit">
            <h2 class="settings-deposit__title">Total deposit from everyone: {{ pounds(deposit.total) }}</h2>
            <p class="settings-deposit__note">
              What you'll have for the next house: expected sale price minus what's owed, plus any
              extra money — added up across the family.
            </p>
            <ul class="settings-deposit__rows">
              <li v-for="row in depositRows" :key="row.name" class="settings-deposit__row">
                <span>{{ row.name }}</span>
                <span>{{ pounds(row.amount) }}</span>
              </li>
            </ul>
            <ProvenanceToggle
              v-if="deposit.provenance"
              :provenance="deposit.provenance"
              title="Household deposit"
            />
          </div>

          <section
            v-if="financial"
            class="settings-finances"
            @input="scheduleFinancialSave"
            @change="scheduleFinancialSave"
            @focusout="flushFinancialSave"
          >
            <h2 class="settings-person__name">Household finances</h2>
            <p class="settings-person__note">
              Shared assumptions every property's monthly cost is built from.
            </p>
            <div class="settings-person__field">
              <label class="settings-person__label" for="mortgage-rate">Mortgage rate (%)</label>
              <input id="mortgage-rate" type="text" inputmode="decimal" v-model="fin['mortgage-rate']" />
            </div>
            <div class="settings-person__field">
              <label class="settings-person__label" for="mortgage-term">Mortgage term (years)</label>
              <input id="mortgage-term" type="text" inputmode="numeric" v-model="fin['mortgage-term']" />
            </div>
            <div class="settings-person__field">
              <label class="settings-person__label" for="sinking-fund">Sinking fund (% of value per year)</label>
              <input id="sinking-fund" type="text" inputmode="decimal" v-model="fin['sinking-fund']" />
            </div>
            <div class="settings-person__field">
              <label class="settings-person__label" for="petrol-cost">Petrol cost (£ per litre)</label>
              <input id="petrol-cost" type="text" inputmode="decimal" v-model="fin['petrol-cost']" />
            </div>
          </section>

          <section
            v-for="person in visiblePersons"
            :key="'money-' + person.name"
            class="settings-person"
            :class="{ 'settings-person--locked': !isOwn(person) }"
            @input="scheduleSave(person)"
            @change="scheduleSave(person)"
            @focusout="flushSave(person)"
          >
            <h3 class="settings-person__subtitle">Your money</h3>
            <div v-if="isOwn(person)" class="settings-person__money">
              <div class="settings-person__field">
                <input id="selling-home" type="checkbox" v-model="person.selling_home" />
                <label class="settings-person__label settings-person__label--inline" for="selling-home">
                  I am selling a home to fund this purchase
                </label>
              </div>
              <template v-if="person.selling_home">
                <label class="settings-person__label" for="home-sale">Expected sale price of current home (£)</label>
                <WholePoundsField
                  id="home-sale"
                  :model-value="person.home_sale_price ? integerPounds(person.home_sale_price.amount) : ''"
                  @update:model-value="(v) => { if (person.home_sale_price) person.home_sale_price.amount = v }"
                />
                <p class="settings-person__helper">What you expect to get when you sell it. Whole pounds only.</p>
                <label class="settings-person__label" for="mortgage">Mortgage remaining on current home (£)</label>
                <WholePoundsField
                  id="mortgage"
                  :model-value="person.outstanding_mortgage ? integerPounds(person.outstanding_mortgage.amount) : ''"
                  @update:model-value="(v) => { if (person.outstanding_mortgage) person.outstanding_mortgage.amount = v }"
                />
                <p class="settings-person__helper">What you still owe on the house you're selling. Whole pounds only.</p>
              </template>
              <p v-else class="settings-person__helper">Deposit is cash — no current home.</p>
              <label class="settings-person__label" for="cash">
                {{ person.selling_home ? 'Other money toward the deposit (£)' : 'Cash available for the deposit (£)' }}
              </label>
              <WholePoundsField
                id="cash"
                :model-value="person.cash_contribution ? integerPounds(person.cash_contribution.amount) : ''"
                @update:model-value="(v) => { if (person.cash_contribution) person.cash_contribution.amount = v }"
              />
              <p class="settings-person__helper">
                {{ person.selling_home ? 'Savings or gifts, on top of the sale proceeds.' : 'Savings, gifts, or the proceeds of a sale.' }}
              </p>
              <label class="settings-person__label" for="life-insurance">Life insurance (£/month)</label>
              <input
                id="life-insurance"
                type="text"
                inputmode="decimal"
                :value="person.life_insurance_monthly?.amount"
                @keydown="blockPenceKey"
                @input="moneyInput(person.life_insurance_monthly!, $event)"
                @blur="penceInput(person.life_insurance_monthly!, $event)"
              />
            </div>
          </section>
        </div>

        <!-- ═══════ COMMUTES ═══════ -->
        <div v-else class="settings-panel" role="tabpanel">
          <section
            v-for="person in visiblePersons"
            :key="'commutes-' + person.name"
            class="settings-person"
            :class="{ 'settings-person--locked': !isOwn(person) }"
            @input="scheduleSave(person)"
            @change="scheduleSave(person)"
            @focusout="flushSave(person)"
          >
            <p v-if="person.is_child && allCommutesAreSchool(person)" class="settings-person__note">
              Goes to school near the house, so school runs are worked out per property.
            </p>

            <div class="settings-person__field">
              <label class="settings-person__label" for="has-car">Has a car</label>
              <input
                id="has-car"
                type="checkbox"
                v-model="person.has_car"
                :disabled="!isOwn(person)"
              />
            </div>
            <div v-if="person.has_car" class="settings-person__field">
              <label class="settings-person__label" for="petrol-mpg">Your car's petrol economy (MPG)</label>
              <input
                id="petrol-mpg"
                type="text"
                inputmode="numeric"
                :value="person.petrol_mpg ?? 45"
                :disabled="!isOwn(person)"
                @input="(e) => { const v = Number((e.target as HTMLInputElement).value); if (Number.isFinite(v) && v > 0) person.petrol_mpg = v }"
              />
            </div>

            <div class="settings-person__thresholds">
              <label class="settings-person__label" for="good-max">Commute is easy up to (minutes)</label>
              <input
                id="good-max"
                type="number"
                :value="thresholds[person.name]?.good_max_minutes"
                :disabled="!isOwn(person)"
                @input="(e) => { const t = thresholds[person.name]; if (t) t.good_max_minutes = Number((e.target as HTMLInputElement).value) }"
              />
              <label class="settings-person__label" for="fine-max">Worst acceptable commute (minutes)</label>
              <input
                id="fine-max"
                type="number"
                :value="thresholds[person.name]?.fine_max_minutes"
                :disabled="!isOwn(person)"
                @input="(e) => { const t = thresholds[person.name]; if (t) t.fine_max_minutes = Number((e.target as HTMLInputElement).value) }"
              />
              <label class="settings-person__label" for="max-walk">Willing to walk up to (minutes)</label>
              <input
                id="max-walk"
                type="number"
                :value="walkMinutes(person)"
                :disabled="!isOwn(person)"
                @input="setWalkMinutes(person, $event)"
              />
              <p class="settings-person__helper">
                These colour the commute pills on the cards: up to the first is 'fine', between the two
                is 'getting tight', over the worst is 'yikes'. Walking is only offered for shorter trips.
              </p>
            </div>

            <h3 class="settings-person__subtitle">Destinations</h3>
            <button
              v-if="isOwn(person)"
              class="poi-add"
              type="button"
              @click="addDestination(person); scheduleSave(person)"
            >+ Add destination</button>
            <div
              v-for="(poi, poiIndex) in person.places_of_interest"
              :key="poiIndex"
              class="settings-poi"
            >
              <button
                v-if="isOwn(person)"
                class="poi-remove"
                type="button"
                :aria-label="'Remove ' + (poi.label || 'destination')"
                @click="removeDestination(person, poiIndex); scheduleSave(person)"
              >×</button>
              <div class="settings-poi__row">
                <label class="settings-person__label" :for="`label-${person.name}-${poi.label}`">Destination name</label>
                <input
                  :id="`label-${person.name}-${poi.label}`"
                  type="text"
                  v-model="poi.label"
                  :disabled="!isOwn(person)"
                />
              </div>
              <p class="settings-person__helper">Shown on cards as '{{ person.name }} → &lt;name&gt;'.</p>
              <div class="settings-poi__row">
                <label class="settings-person__label" :for="`address-${person.name}-${poi.label}`">Address</label>
                <input
                  :id="`address-${person.name}-${poi.label}`"
                  type="text"
                  v-model="poi.address"
                  :disabled="!isOwn(person)"
                />
              </div>
              <p class="settings-person__helper">Used to calculate the commute.</p>
              <div class="settings-poi__row">
                <label class="settings-person__label" :for="`trips-${person.name}-${poi.label}`">Trips per week</label>
                <input
                  :id="`trips-${person.name}-${poi.label}`"
                  type="number"
                  v-model.number="poi.trips_per_week"
                  :disabled="!isOwn(person)"
                />
              </div>
              <div class="settings-poi__row">
                <label class="settings-person__label" :for="`weeks-${person.name}-${poi.label}`">Weeks per year</label>
                <input
                  :id="`weeks-${person.name}-${poi.label}`"
                  type="number"
                  v-model.number="poi.weeks_per_year"
                  :disabled="!isOwn(person)"
                />
              </div>
              <div class="settings-poi__modes">
                <span class="settings-person__label">Transport modes you'd accept</span>
                <label
                  v-for="mode in MODE_OPTIONS"
                  :key="mode.value"
                  class="settings-poi__mode"
                  :class="{ 'settings-poi__mode--hidden': mode.value === 'car' && !person.has_car }"
                >
                  <input
                    type="checkbox"
                    :data-mode="mode.value"
                    :checked="poi.acceptable_modes.includes(mode.value)"
                    :disabled="!isOwn(person) || (mode.value === 'car' && !person.has_car)"
                    @change="isOwn(person) && toggleMode(poi, mode.value)"
                  />
                  {{ mode.label }}
                </label>
                <p class="settings-person__helper">The app plans routes using only these.</p>
              </div>
            </div>
            <p v-if="person.places_of_interest.length === 0" class="settings-poi__empty">
              No regular commutes.
            </p>
          </section>
        </div>

        <p v-if="!anyEditable" class="settings__note">
          Your account isn't linked to a person yet — ask the family's superuser to add your email in
          their settings.
        </p>
      </template>
    </main>
  </div>
</template>

<style scoped>
.settings-person__strip { margin: var(--sp-2) 0 0; }

.settings__main {
  max-width: 860px;
  margin: 0 auto;
  padding: 1rem;
}
.settings__intro,
.settings__note,
.settings__status,
.settings__error {
  color: var(--text-muted);
}
.settings__error {
  color: var(--red);
}
.settings-person {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem;
  margin: 1rem 0;
}
.settings-person--locked {
  opacity: 0.75;
}
.settings-person--target {
  border-color: var(--blue);
  box-shadow: 0 0 0 2px var(--blue, #2f6fed) inset;
}
.settings-person__header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.settings-person__name {
  margin: 0;
}
.settings-person__badge {
  background: var(--blue);
  color: white;
  border-radius: var(--radius-full);
  padding: 0.1rem 0.6rem;
  font-size: 0.75rem;
}
.settings-person__badge--child {
  background: var(--amber);
}
.settings-person__locked-note {
  color: var(--text-muted);
  font-size: 0.85rem;
}
.settings-person__status {
  margin-left: auto;
  font-size: 0.85rem;
  color: var(--text-muted);
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}
.settings-person__status--saved {
  color: var(--green);
}
.settings-person__status--error {
  color: var(--red);
}
.settings-person__undo {
  background: transparent;
  border: 1px solid currentColor;
  border-radius: 6px;
  font-size: 0.75rem;
  padding: 0.1rem 0.5rem;
  cursor: pointer;
}
.settings-deposit {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem;
  margin: 1rem 0;
  background: var(--bg-subtle, #fafafa);
}
.settings-deposit__title {
  margin: 0 0 0.25rem;
}
.settings-deposit__note {
  color: var(--text-muted);
  font-size: 0.9rem;
  margin: 0 0 0.75rem;
}
.settings-deposit__rows {
  list-style: none;
  margin: 0;
  padding: 0;
}
.settings-deposit__row {
  display: flex;
  justify-content: space-between;
  padding: 0.2rem 0;
  border-top: 1px dashed var(--border);
}
.settings-person__helper {
  color: var(--text-muted);
  font-size: 0.8rem;
  margin: 0 0 0.5rem;
}
.settings-person__note,
.settings-poi__empty {
  color: var(--text-muted);
  font-size: 0.9rem;
}
.settings-person__field,
.settings-person__thresholds,
.settings-poi__row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin: 0.5rem 0;
}
.settings-person__label {
  min-width: 11rem;
  color: var(--text-muted);
  font-size: 0.85rem;
}
.settings-person__label--inline {
  min-width: 0;
}
.settings-person__money input,
.settings-person__thresholds input,
.settings-poi__row input {
  flex: 1;
  max-width: 14rem;
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.settings-person__subtitle {
  margin: 1rem 0 0.25rem;
  font-size: 1rem;
}
.settings-poi {
  border-top: 1px dashed var(--border);
  padding: 0.5rem 0;
  position: relative;
}
.poi-add {
  background: none;
  border: 1px dashed var(--border);
  border-radius: 6px;
  color: var(--blue);
  cursor: pointer;
  padding: 0.3rem 0.7rem;
  margin: 0.4rem 0;
}
.poi-remove {
  position: absolute;
  top: 0.4rem;
  right: 0;
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 1.1rem;
  padding: 0 0.3rem;
}
.settings-poi__modes {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin: 0.4rem 0;
}
.settings-poi__mode--hidden {
  display: none;
}
.settings__back {
  color: var(--blue);
  text-decoration: none;
}
</style>