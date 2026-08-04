<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import Header from '../components/Header.vue'
import * as api from '../services/api'

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
  places_of_interest: PoiSettings[]
  home_sale_price?: MoneyValue
  outstanding_mortgage?: MoneyValue
  cash_contribution?: MoneyValue
  life_insurance_monthly?: MoneyValue
}

interface Thresholds {
  good_max_minutes: number
  fine_max_minutes: number
}

const loading = ref(true)
const error = ref('')
const persons = ref<PersonSettings[]>([])
const thresholds = ref<Record<string, Thresholds>>({})
const savedFor = ref<string>('')

// The three ways a commute can happen. "Car" is only offered to people
// who have one; "walk" is always an option (walking is accepted even when
// they also drive — the map draws it only when they don't).
const MODE_OPTIONS = [
  { value: 'train', label: 'Trains' },
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
    }
    persons.value = data.persons?.value ?? []
    thresholds.value = data.commute_thresholds?.value ?? {}
  } catch {
    error.value = 'Could not load the family settings.'
  } finally {
    loading.value = false
  }
}

function toggleMode(poi: PoiSettings, mode: string) {
  const set = new Set(poi.acceptable_modes)
  if (set.has(mode)) set.delete(mode)
  else set.add(mode)
  poi.acceptable_modes = MODE_OPTIONS.map(o => o.value).filter(m => set.has(m))
}

function moneyInput(money: MoneyValue | undefined, event: Event) {
  if (money) money.amount = String((event.target as HTMLInputElement).value)
}

function allCommutesAreSchool(person: PersonSettings): boolean {
  return person.places_of_interest.length > 0 && person.places_of_interest.every(p => p.address === '')
}

function isOwn(person: PersonSettings): boolean {
  return person.editable_by_me === true
}

async function save(person: PersonSettings) {
  const body: Record<string, unknown> = {
    name: person.name,
    has_car: person.has_car,
    is_child: person.is_child,
    email: person.email,
    is_superuser: person.is_superuser,
    places_of_interest: person.places_of_interest,
  }
  for (const f of ['home_sale_price', 'outstanding_mortgage', 'cash_contribution', 'life_insurance_monthly'] as const) {
    if (person[f]) body[f] = person[f]
  }
  const t = thresholds.value[person.name]
  if (t) body.thresholds = { ...t }
  try {
    await api.patchPerson(person.name, body)
    savedFor.value = person.name
    setTimeout(() => (savedFor.value = ''), 2500)
  } catch {
    error.value = `Could not save ${person.name}'s settings.`
  }
}

const anyEditable = computed(() => persons.value.some(isOwn))
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
          Everything the commute map is built from lives here. Everyone's commutes are shown so the
          household can see the whole picture — you can only change your own.
        </p>

        <section
          v-for="person in persons"
          :key="person.name"
          class="settings-person"
          :class="{ 'settings-person--locked': !isOwn(person) }"
        >
          <header class="settings-person__header">
            <h2 class="settings-person__name">{{ person.name }}</h2>
            <span v-if="isOwn(person)" class="settings-person__badge">you</span>
            <span v-if="person.is_child" class="settings-person__badge settings-person__badge--child">child</span>
            <span v-if="!isOwn(person)" class="settings-person__locked-note">read-only</span>
            <button v-if="isOwn(person)" class="save" @click="save(person)">Save</button>
            <span v-if="savedFor === person.name" class="settings-person__saved">Saved ✓</span>
          </header>

          <p v-if="person.is_child && allCommutesAreSchool(person)" class="settings-person__note">
            Goes to school near the house — no fixed address, so school runs are worked out per property.
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

          <div v-if="isOwn(person)" class="settings-person__money">
            <label class="settings-person__label" for="home-sale">Current home value (£)</label>
            <input
              id="home-sale"
              type="number"
              :value="person.home_sale_price?.amount"
              @input="moneyInput(person.home_sale_price!, $event)"
            />
            <label class="settings-person__label" for="mortgage">Outstanding mortgage (£)</label>
            <input
              id="mortgage"
              type="number"
              :value="person.outstanding_mortgage?.amount"
              @input="moneyInput(person.outstanding_mortgage!, $event)"
            />
            <label class="settings-person__label" for="cash">Cash contribution (£)</label>
            <input
              id="cash"
              type="number"
              :value="person.cash_contribution?.amount"
              @input="moneyInput(person.cash_contribution!, $event)"
            />
            <label class="settings-person__label" for="life-insurance">Life insurance (£/month)</label>
            <input
              id="life-insurance"
              type="number"
              :value="person.life_insurance_monthly?.amount"
              @input="moneyInput(person.life_insurance_monthly!, $event)"
            />
          </div>

          <div class="settings-person__thresholds">
            <label class="settings-person__label" for="fine-max">Worst acceptable commute (minutes)</label>
            <input
              id="fine-max"
              type="number"
              :value="thresholds[person.name]?.fine_max_minutes"
              :disabled="!isOwn(person)"
              @input="(e) => { const t = thresholds[person.name]; if (t) t.fine_max_minutes = Number((e.target as HTMLInputElement).value) }"
            />
          </div>

          <h3 class="settings-person__subtitle">Commutes</h3>
          <div
            v-for="poi in person.places_of_interest"
            :key="poi.label"
            class="settings-poi"
          >
            <div class="settings-poi__row">
              <label class="settings-person__label" :for="`label-${person.name}-${poi.label}`">Place</label>
              <input
                :id="`label-${person.name}-${poi.label}`"
                type="text"
                v-model="poi.label"
                :disabled="!isOwn(person)"
              />
            </div>
            <div class="settings-poi__row">
              <label class="settings-person__label" :for="`address-${person.name}-${poi.label}`">Address</label>
              <input
                :id="`address-${person.name}-${poi.label}`"
                type="text"
                v-model="poi.address"
                :disabled="!isOwn(person)"
                :placeholder="poi.address === '' ? 'no fixed address' : ''"
              />
            </div>
            <div class="settings-poi__row">
              <label class="settings-person__label" :for="`trips-${person.name}-${poi.label}`">Trips per week</label>
              <input
                :id="`trips-${person.name}-${poi.label}`"
                type="number"
                v-model.number="poi.trips_per_week"
                :disabled="!isOwn(person)"
              />
            </div>
            <div class="settings-poi__modes">
              <span class="settings-person__label">How they get there</span>
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
            </div>
          </div>
          <p v-if="person.places_of_interest.length === 0" class="settings-poi__empty">
            No regular commutes.
          </p>
        </section>

        <p v-if="!anyEditable" class="settings__note">
          Your account isn't linked to a person yet — ask the family's superuser to add your email in
          their settings.
        </p>
      </template>
    </main>
  </div>
</template>

<style scoped>
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
  border-radius: 999px;
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
.settings-person__saved {
  color: var(--green);
  font-size: 0.85rem;
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
button.save {
  margin-left: auto;
  background: var(--blue);
  color: white;
  border: none;
  border-radius: 8px;
  padding: 0.4rem 1.2rem;
  cursor: pointer;
}
.settings__back {
  color: var(--blue);
  text-decoration: none;
}
</style>
