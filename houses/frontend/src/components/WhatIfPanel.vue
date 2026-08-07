<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
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

const props = defineProps<{ threshold?: number }>()

const store = usePropertiesStore()
const persons = ref<PersonEdit[]>([])
const busy = ref(false)
const errorMsg = ref('')
const collapsed = ref(true)
const activeTab = ref<'finances' | 'commutes'>('finances')
let timer: ReturnType<typeof setTimeout> | null = null

const active = computed(() => store.whatIfTotals != null)

const threshold = computed(() => props.threshold ?? 1500)

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
}

// ── Evaluation (debounced) ─────────────────────────────────────

function scheduleEval() {
  if (timer) clearTimeout(timer)
  timer = setTimeout(run, 400)
}

function payload() {
  return persons.value.map(p => {
    const body: Record<string, unknown> = {
      name: p.name,
      selling_home: p.selling_home,
      petrol_mpg: p.petrol_mpg,
      bus_walk_penalty: { ...p.bus_walk_penalty },
    }
    if (p.selling_home) {
      body.home_sale_price = money(p.home_sale_price)
      body.outstanding_mortgage = money(p.outstanding_mortgage)
    }
    body.cash_contribution = money(p.cash_contribution)
    body.life_insurance_monthly = money(p.life_insurance_monthly || '0')
    body.places_of_interest = p.places_of_interest.map(poi => ({ ...poi }))
    return body
  })
}

async function run() {
  busy.value = true
  errorMsg.value = ''
  try {
    const results = await api.postWhatIf(payload())
    store.applyWhatIf(results)
  } catch {
    errorMsg.value = "Couldn't run the what-if."
  } finally {
    busy.value = false
  }
}

// ── Delta headline ─────────────────────────────────────────────

const realOf = (rid: string): number | null => {
  const s = store.summaries[rid]?.total_monthly_cost
  if (!s?.succeeded || !s.value) return null
  return parseFloat(s.value.value.amount)
}

const deltaHeadline = computed(() => {
  const totals = store.whatIfTotals
  if (!totals) return ''
  let realUnder = 0
  let hypoUnder = 0
  for (const rid of store.rids) {
    const real = realOf(rid)
    if (real != null && real <= threshold.value) realUnder++
    const hypo = totals[rid] ? parseFloat(totals[rid].value.amount) : null
    if (hypo != null && hypo <= threshold.value) hypoUnder++
  }
  const diff = hypoUnder - realUnder
  const label = `under £${threshold.value.toLocaleString()}/mo`
  if (diff > 0) return `${diff} more house${diff === 1 ? '' : 's'} ${label}`
  if (diff < 0) return `${-diff} fewer house${diff === -1 ? '' : 's'} ${label}`
  return `No change in houses ${label}`
})

// ── Commit / exit ──────────────────────────────────────────────

async function useTheseNumbers() {
  busy.value = true
  errorMsg.value = ''
  try {
    for (const p of persons.value) {
      const body: Record<string, unknown> = {
        name: p.name,
        selling_home: p.selling_home,
        petrol_mpg: p.petrol_mpg,
        bus_walk_penalty: { ...p.bus_walk_penalty },
      }
      if (p.selling_home) {
        body.home_sale_price = money(p.home_sale_price)
        body.outstanding_mortgage = money(p.outstanding_mortgage)
      }
      body.cash_contribution = money(p.cash_contribution)
      body.life_insurance_monthly = money(p.life_insurance_monthly || '0')
      body.places_of_interest = p.places_of_interest.map(poi => ({ ...poi }))
      await api.patchPerson(p.name, body)
    }
    store.clearWhatIf()
    await store.loadAll()
  } catch {
    errorMsg.value = "Couldn't save the what-if values."
  } finally {
    busy.value = false
  }
}

function backToReal() {
  store.clearWhatIf()
}
</script>

<template>
  <section class="whatif" :class="{ 'whatif--collapsed': collapsed }" aria-label="What if">
    <header class="whatif__header">
      <button class="whatif__toggle" type="button" @click="collapsed = !collapsed">
        <h2 class="whatif__title">What if…</h2>
        <span class="whatif__chevron" aria-hidden="true">{{ collapsed ? '▸' : '▾' }}</span>
      </button>
      <span v-if="active" class="whatif__badge">not saved</span>
    </header>

    <template v-if="!collapsed">
      <p v-if="active" class="whatif__delta">{{ deltaHeadline }}</p>
      <p class="whatif__intro">
        Try different numbers without changing the family settings. Nothing is saved until you
        choose "Use these numbers". Money is in whole pounds.
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

      <div v-if="activeTab === 'finances'" class="settings-panel" role="tabpanel">
        <div v-for="p in persons" :key="p.name" class="settings-card whatif-person">
          <div class="card-heading">
            {{ p.name }}
            <label class="toggle-row" style="margin-top: var(--sp-2)">
              <span class="toggle-row__label">Selling a home to fund this purchase</span>
              <ToggleSwitch v-model="p.selling_home" @change="scheduleEval" />
            </label>
          </div>

          <div v-if="p.selling_home" class="whatif-person__fields">
            <label class="whatif-person__field">
              Expected sale price (£)
              <WholePoundsField v-model="p.home_sale_price" @input="scheduleEval" />
              <span class="band-helper">What you expect to get when you sell it. Whole pounds only.</span>
            </label>
            <label class="whatif-person__field">
              Mortgage remaining (£)
              <WholePoundsField v-model="p.outstanding_mortgage" @input="scheduleEval" />
              <span class="band-helper">What you still owe on the home you're selling. Whole pounds only.</span>
            </label>
          </div>

          <label class="whatif-person__field">
            {{ p.selling_home ? 'Other money toward the deposit (£)' : 'Cash available for the deposit (£)' }}
            <WholePoundsField v-model="p.cash_contribution" @input="scheduleEval" />
            <span class="band-helper">{{ p.selling_home ? 'Savings or gifts, on top of the sale proceeds.' : 'Savings, gifts, or the proceeds of a sale.' }}</span>
          </label>
          <label class="whatif-person__field">
            Life insurance (£/month)
            <input
              type="text"
              inputmode="decimal"
              :value="p.life_insurance_monthly"
              @keydown="blockPenceKey"
              @input="(e) => { p.life_insurance_monthly = (e.target as HTMLInputElement).value; scheduleEval() }"
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
            <ToggleSwitch v-model="p.has_car" @change="scheduleEval" />
          </label>
          <label v-if="p.has_car" class="whatif-person__field">
            Your car's petrol economy (MPG)
            <input
              v-model.number="p.petrol_mpg"
              type="number" min="1" inputmode="numeric"
              @input="scheduleEval"
            />
          </label>
          <label class="whatif-person__field">
            Willing to walk up to (minutes)
            <input
              v-model.number="p.bus_walk_penalty.value"
              type="number" min="0" inputmode="numeric"
              @input="scheduleEval"
            />
          </label>
          <div v-if="p.places_of_interest.length" class="whatif-person__pois">
            <label v-for="poi in p.places_of_interest" :key="poi.label" class="whatif-person__field">
              {{ poi.label }} — days per week
              <input
                v-model.number="poi.trips_per_week"
                type="number" min="0" max="7" inputmode="numeric"
                @input="scheduleEval"
              />
            </label>
          </div>
        </div>
      </div>

    <p v-if="busy" class="whatif__status">Updating…</p>
    <p v-if="errorMsg" class="whatif__error">{{ errorMsg }}</p>

    <footer class="whatif__footer">
      <button class="whatif__btn whatif__btn--ghost" :disabled="!active" @click="backToReal">
        Back to real numbers
      </button>
      <button class="whatif__btn whatif__btn--primary" :disabled="!active || busy" @click="useTheseNumbers">
        Use these numbers
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
.whatif__chevron {
  font-size: 0.8rem;
  color: var(--text-muted);
}
.whatif__title {
  margin: 0;
  font-size: 0.875rem;
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.whatif__badge {
  font-size: 0.6875rem;
  font-weight: var(--fw-semibold);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #fff;
  background: var(--blue);
  border-radius: var(--radius-full);
  padding: 0.15rem 0.6rem;
  white-space: nowrap;
}
.whatif__delta {
  padding: 10px 12px;
  background: var(--green-bg);
  border-radius: var(--radius-sm);
  font-size: 0.875rem;
  font-weight: var(--fw-semibold);
  color: var(--green-text);
  margin: 10px 0 0;
  text-align: center;
}
.whatif__intro {
  margin: 0.6rem 0 0.4rem;
  font-size: 0.8125rem;
  color: var(--text-muted);
}
.whatif-person {
  border-top: 1px solid var(--divider);
  padding: 0.6rem 0;
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
