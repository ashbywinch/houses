<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import * as api from '../services/api'
import { usePropertiesStore } from '../stores/properties'
import { blockPenceKey, integerPounds, normalizePence } from '../formatters/money'
import ToggleSwitch from './ToggleSwitch.vue'
import WholePoundsField from './WholePoundsField.vue'

// ── Editable person shape (a question, not a form) ─────────────

interface PoiEdit {
  label: string
  address: string
  trips_per_week: number
  weeks_per_year: number
  acceptable_modes: string[]
}

interface PersonEdit {
  name: string
  selling_home: boolean
  has_car: boolean
  petrol_mpg: number
  bus_walk_penalty: { value: number; unit: string }
  home_sale_price: string
  outstanding_mortgage: string
  cash_contribution: string
  life_insurance_monthly: string
  places_of_interest: PoiEdit[]
}

const store = usePropertiesStore()
const persons = ref<PersonEdit[]>([])
const busy = ref(false)
const errorMsg = ref('')
const collapsed = ref(true)
const activeTab = ref<'finances' | 'commutes'>('finances')

// The panel edits local copies and only touches the server from the
// two footer buttons — nothing is evaluated while typing.
const active = computed(() => store.whatIfActive)

// While a what-if is active the panel is pinned open — the exits live
// in its footer, so it stays up until the mode is resolved (toggle,
// reload, it doesn't matter: Back or Keep are the only ways out).
watch(active, v => {
  if (v) collapsed.value = false
}, { immediate: true })

function toggleCollapsed() {
  if (!active.value) collapsed.value = !collapsed.value
}

function money(amount: string): { amount: string; currency: string } {
  return { amount, currency: 'GBP' }
}

onMounted(load)

async function load() {
  try {
    const data = (await api.fetchSettings()) as { persons?: { value?: Record<string, unknown>[] } }
    persons.value = (data.persons?.value ?? [])
      // children have no finances — never show them in the what-if
      .filter(p => !p.is_child)
      .map(p => ({
        name: String(p.name),
        selling_home: Boolean(p.selling_home),
        has_car: Boolean(p.has_car),
        petrol_mpg: Number((p as Record<string, unknown>).petrol_mpg ?? 45),
        bus_walk_penalty: {
          value: Number(((p as Record<string, unknown>).bus_walk_penalty as { value?: number } | undefined)?.value ?? 20),
          unit: 'minute',
        },
        home_sale_price: integerPounds((p.home_sale_price as { amount?: string } | undefined)?.amount),
        outstanding_mortgage: integerPounds((p.outstanding_mortgage as { amount?: string } | undefined)?.amount),
        cash_contribution: integerPounds((p.cash_contribution as { amount?: string } | undefined)?.amount),
        life_insurance_monthly: String((p.life_insurance_monthly as { amount?: string } | undefined)?.amount ?? ''),
        places_of_interest: ((p.places_of_interest as PoiEdit[] | undefined) ?? []).map(poi => ({
          label: poi.label,
          address: poi.address,
          trips_per_week: poi.trips_per_week ?? 1,
          weeks_per_year: poi.weeks_per_year ?? 46,
          acceptable_modes: [...poi.acceptable_modes],
        })),
      }))
  } catch {
    errorMsg.value = "Couldn't load the family settings."
  }
  try {
    store.setWhatIfActive(await api.fetchWhatIfState())
  } catch {
    // best-effort — keep the last known mode
  }
}

// ── Apply / back / keep (the only writes) ─────────────────────

function payload() {
  return persons.value.map(p => {
    const body: Record<string, unknown> = {
      name: p.name,
      selling_home: p.selling_home,
      has_car: p.has_car,
      petrol_mpg: p.petrol_mpg,
      bus_walk_penalty: { ...p.bus_walk_penalty },
    }
    if (p.selling_home) {
      body.home_sale_price = money(p.home_sale_price || '0')
      body.outstanding_mortgage = money(p.outstanding_mortgage || '0')
    }
    body.cash_contribution = money(p.cash_contribution || '0')
    body.life_insurance_monthly = money(p.life_insurance_monthly || '0')
    body.places_of_interest = p.places_of_interest.map(poi => ({ ...poi }))
    return body
  })
}

/** Writes the scenario persons through the NORMAL settings write: the
 *  DAG recomputes every number server-side and the existing websocket
 *  broadcast refreshes every surface. */
async function apply() {
  busy.value = true
  errorMsg.value = ''
  try {
    await api.applyWhatIf(payload())
    store.setWhatIfActive(true)
  } catch {
    errorMsg.value = "Couldn't apply the what-if."
  } finally {
    busy.value = false
  }
}

/** Puts the original numbers back — the DAG recomputes server-side
 *  and the websocket broadcast refreshes every surface. */
async function restore() {
  busy.value = true
  errorMsg.value = ''
  try {
    await api.restoreWhatIf()
    store.setWhatIfActive(false)
  } catch {
    errorMsg.value = "Couldn't restore the real numbers."
  } finally {
    busy.value = false
  }
}

/** Keeps the scenario as the new real numbers — the server discards
 *  the restore snapshot. */
async function accept() {
  busy.value = true
  errorMsg.value = ''
  try {
    await api.acceptWhatIf()
    store.setWhatIfActive(false)
  } catch {
    errorMsg.value = "Couldn't accept the what-if numbers."
  } finally {
    busy.value = false
  }
}

</script>

<template>
  <section class="whatif" :class="{ 'whatif--collapsed': collapsed, 'whatif--pinned': active }" aria-label="What if">
    <header class="whatif__header">
      <button
        class="whatif__toggle"
        type="button"
        :disabled="active"
        title="Resolve the what-if first"
        @click="toggleCollapsed"
      >
        <h2 class="whatif__title">What if…</h2>
        <span class="whatif__chevron" aria-hidden="true">{{ collapsed ? '▸' : '▾' }}</span>
      </button>
    </header>

    <template v-if="!collapsed">
      <p class="whatif__intro">
        What-if changes your saved numbers everywhere — on every card and page — until you go back.
        Your original numbers are kept and come back with one click. Money is in whole pounds.
      </p>

      <!-- Same two tabs as the settings page: Finances | Commutes -->
      <nav class="settings-tabs" role="tablist" aria-label="What-if sections">
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

      <fieldset class="whatif__fieldset" :disabled="active">
      <div v-if="activeTab === 'finances'" class="settings-panel" role="tabpanel">
        <div v-for="p in persons" :key="p.name" class="settings-card whatif-person">
          <div class="card-heading">{{ p.name }}</div>
          <label class="toggle-row">
            <span class="toggle-row__label">Selling a home to fund this purchase</span>
            <ToggleSwitch v-model="p.selling_home" />
          </label>

          <div v-if="p.selling_home" class="whatif-person__fields">
            <label class="whatif-person__field">
              Expected sale price (£)
              <WholePoundsField v-model="p.home_sale_price" />
              <span class="band-helper">What you expect to get when you sell it. Whole pounds only.</span>
            </label>
            <label class="whatif-person__field">
              Mortgage remaining (£)
              <WholePoundsField v-model="p.outstanding_mortgage" />
              <span class="band-helper">What you still owe on the home you're selling. Whole pounds only.</span>
            </label>
          </div>

          <label class="whatif-person__field">
            {{ p.selling_home ? 'Other money toward the deposit (£)' : 'Cash available for the deposit (£)' }}
            <WholePoundsField v-model="p.cash_contribution" />
            <span class="band-helper">{{ p.selling_home ? 'Savings or gifts, on top of the sale proceeds.' : 'Savings, gifts, or the proceeds of a sale.' }}</span>
          </label>
          <label class="whatif-person__field">
            Life insurance (£/month)
            <input
              type="text"
              inputmode="decimal"
              :value="p.life_insurance_monthly"
              @keydown="blockPenceKey"
              @input="(e) => { p.life_insurance_monthly = (e.target as HTMLInputElement).value }"
              @blur="(e) => { p.life_insurance_monthly = normalizePence((e.target as HTMLInputElement).value) }"
            />
            <span class="band-helper">Pence allowed.</span>
          </label>
        </div>
      </div>

      <div v-else class="settings-panel" role="tabpanel">
        <div v-for="p in persons" :key="p.name" class="settings-card dest-card whatif-person">
          <label class="toggle-row">
            <span class="toggle-row__label">Has a car</span>
            <ToggleSwitch v-model="p.has_car" />
          </label>
          <label v-if="p.has_car" class="whatif-person__field">
            Your car's petrol economy (MPG)
            <input
              v-model.number="p.petrol_mpg"
              type="number" min="1" inputmode="numeric"
            />
          </label>
          <label class="whatif-person__field">
            Willing to walk up to (minutes)
            <input
              v-model.number="p.bus_walk_penalty.value"
              type="number" min="0" inputmode="numeric"
            />
          </label>
          <div v-if="p.places_of_interest.length" class="whatif-person__pois">
            <label v-for="poi in p.places_of_interest" :key="poi.label" class="whatif-person__field">
              {{ poi.label }} — days per week
              <input
                v-model.number="poi.trips_per_week"
                type="number" min="0" max="7" inputmode="numeric"
              />
            </label>
          </div>
        </div>
      </div>
      </fieldset>

    <p v-if="busy" class="whatif__status">Saving…</p>
    <p v-if="errorMsg" class="whatif__error">{{ errorMsg }}</p>

    <footer class="whatif__footer">
      <button v-if="!active" class="whatif__btn whatif__btn--primary" :disabled="busy" @click="apply">
        Try scenario
      </button>
      <button v-if="active" class="whatif__btn whatif__btn--ghost" :disabled="busy" @click="restore">
        Back to real numbers
      </button>
      <button v-if="active" class="whatif__btn whatif__btn--ghost" :disabled="busy" @click="accept">
        Keep these numbers
      </button>
    </footer>
    </template>
  </section>
</template>

<style scoped>
.whatif {
  padding: 12px 0 0;
}
.whatif__header {
  display: flex;
  align-items: center;
  width: 100%;
}
.whatif__toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-height: 44px;
  padding: 12px 14px;
  border: 1.5px dashed var(--text-muted);
  border-radius: var(--radius);
  background: var(--card-bg);
  cursor: pointer;
  transition: background 0.15s;
  color: inherit;
  font: inherit;
}
.whatif__toggle:hover {
  background: var(--pill-bg);
}

.whatif--pinned .whatif__toggle {
  border-style: solid;
  cursor: default;
}

.whatif--pinned .whatif__toggle:hover {
  background: var(--card-bg);
}
.whatif__chevron {
  font-size: 0.8rem;
  color: var(--text-muted);
}

.whatif__fieldset {
  border: none;
  padding: 0;
  margin: 0;
  min-width: 0;
}
.whatif__title {
  margin: 0;
  font-size: 0.875rem;
  font-weight: var(--fw-semibold);
}
.whatif__intro {
  margin: 0.6rem 0 0.4rem;
  font-size: 0.8125rem;
  color: var(--text-muted);
}
.whatif-person__head {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1rem;
  align-items: center;
  margin-bottom: 0.4rem;
}
.whatif-person__toggle {
  font-size: 0.85rem;
  display: flex;
  align-items: center;
  gap: 0.3rem;
}
.whatif-person__fields {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem 1rem;
}
.whatif-person__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin-bottom: var(--sp-2);
  color: var(--text-secondary);
  font-size: var(--fs-sm);
}
.whatif-person__field input {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-base);
  font-family: inherit;
  color: var(--text);
  background: var(--card-bg);
  min-height: 44px;
  box-sizing: border-box;
  width: 100%;
}
.whatif-person__field .band-helper { font-size: var(--fs-2xs); }
.whatif-person__field input {
  padding: 4px 10px;
  border: none;
  background: var(--pill-bg);
  border-radius: var(--radius-sm);
  font-size: 0.875rem;
  font-weight: var(--fw-semibold);
  color: var(--text);
  text-align: right;
  outline: none;
  min-height: 32px;
}
.whatif__status {
  color: var(--text-muted);
  font-size: 0.85rem;
  margin: 0.4rem 0 0;
}
.whatif__error {
  color: var(--red);
  font-size: 0.85rem;
  margin: 0.4rem 0 0;
}
.whatif__footer {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}
.whatif__btn {
  flex: 1;
  border: none;
  border-radius: var(--radius-sm);
  padding: 10px;
  font-size: 0.8125rem;
  font-weight: var(--fw-semibold);
  cursor: pointer;
  text-align: center;
}
.whatif__btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.whatif__btn--ghost {
  background: var(--pill-bg);
  color: var(--text-secondary);
}
.whatif__btn--primary {
  background: var(--blue);
  color: #fff;
}

</style>
