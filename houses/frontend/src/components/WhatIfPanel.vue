<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import * as api from '../services/api'
import { usePropertiesStore } from '../stores/properties'

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
  home_sale_price: string
  outstanding_mortgage: string
  cash_contribution: string
  places_of_interest: PoiEdit[]
}

const props = defineProps<{ threshold?: number }>()

const store = usePropertiesStore()
const persons = ref<PersonEdit[]>([])
const busy = ref(false)
const errorMsg = ref('')
const collapsed = ref(true)
let timer: ReturnType<typeof setTimeout> | null = null

const active = computed(() => store.whatIfTotals != null)

const threshold = computed(() => props.threshold ?? 1500)

function money(amount: string): { amount: string; currency: string } {
  return { amount, currency: 'GBP' }
}

/** Money displays and sends as integer pounds — no decimals. */
function integerPounds(amount: string | undefined): string {
  if (amount == null || amount === '') return ''
  return String(Math.round(Number(amount)))
}

/** Keep money inputs as strings on the wire (the API convention). */
function asString(e: Event): string {
  return (e.target as HTMLInputElement).value
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
        home_sale_price: integerPounds((p.home_sale_price as { amount?: string } | undefined)?.amount),
        outstanding_mortgage: integerPounds((p.outstanding_mortgage as { amount?: string } | undefined)?.amount),
        cash_contribution: integerPounds((p.cash_contribution as { amount?: string } | undefined)?.amount),
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
    const body: Record<string, unknown> = { name: p.name, selling_home: p.selling_home }
    if (p.selling_home) {
      body.home_sale_price = money(p.home_sale_price)
      body.outstanding_mortgage = money(p.outstanding_mortgage)
    }
    body.cash_contribution = money(p.cash_contribution)
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
      const body: Record<string, unknown> = { name: p.name, selling_home: p.selling_home }
      if (p.selling_home) {
        body.home_sale_price = money(p.home_sale_price)
        body.outstanding_mortgage = money(p.outstanding_mortgage)
      }
      body.cash_contribution = money(p.cash_contribution)
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
      <p class="whatif__intro">
        Try different numbers without changing the family settings. Nothing is saved until you
        choose "Use these numbers".
      </p>

    <div class="whatif__persons">
      <div v-for="p in persons" :key="p.name" class="whatif-person">
        <div class="whatif-person__head">
          <strong>{{ p.name }}</strong>
          <label class="whatif-person__toggle">
            <input v-model="p.selling_home" type="checkbox" @change="scheduleEval" />
            Selling a home to fund this purchase
          </label>
        </div>

        <div v-if="p.selling_home" class="whatif-person__fields">
          <label class="whatif-person__field">
            Expected sale price (£)
            <input
              :value="p.home_sale_price" type="number" inputmode="numeric"
              @input="p.home_sale_price = asString($event); scheduleEval()"
            />
          </label>
          <label class="whatif-person__field">
            Mortgage remaining (£)
            <input
              :value="p.outstanding_mortgage" type="number" inputmode="numeric"
              @input="p.outstanding_mortgage = asString($event); scheduleEval()"
            />
          </label>
        </div>

        <label class="whatif-person__field">
          Cash available for the deposit (£)
          <input
            :value="p.cash_contribution" type="number" inputmode="numeric"
            @input="p.cash_contribution = asString($event); scheduleEval()"
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

    <p v-if="active" class="whatif__delta">{{ deltaHeadline }}</p>
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
  border: 1px dashed var(--blue);
  border-radius: 12px;
  padding: 1rem;
  margin: 0 1rem 1rem;
  background: color-mix(in srgb, var(--blue) 6%, white);
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
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  color: inherit;
  font: inherit;
}
.whatif__chevron {
  font-size: 0.8rem;
}
.whatif--collapsed {
  padding: 0.6rem 1rem;
}
.whatif__title {
  margin: 0;
  font-size: 1.05rem;
}
.whatif__badge {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: white;
  background: var(--blue);
  border-radius: 999px;
  padding: 0.15rem 0.6rem;
}
.whatif__intro {
  margin: 0.4rem 0 0.8rem;
  font-size: 0.85rem;
  color: var(--text-muted, #666);
}
.whatif-person {
  border-top: 1px solid var(--border, #ddd);
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
  font-size: 0.8rem;
  gap: 0.2rem;
  margin-right: 1rem;
  min-width: 10rem;
}
.whatif-person__field input {
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--border, #ccc);
  border-radius: 6px;
}
.whatif__delta {
  font-weight: 600;
  margin: 0.6rem 0 0;
}
.whatif__status {
  color: var(--text-muted, #666);
  font-size: 0.85rem;
  margin: 0.4rem 0 0;
}
.whatif__error {
  color: var(--red, #c0392b);
  font-size: 0.85rem;
  margin: 0.4rem 0 0;
}
.whatif__footer {
  display: flex;
  gap: 0.6rem;
  margin-top: 0.8rem;
}
.whatif__btn {
  border: none;
  border-radius: 8px;
  padding: 0.45rem 0.9rem;
  font-size: 0.85rem;
  cursor: pointer;
}
.whatif__btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.whatif__btn--ghost {
  background: transparent;
  border: 1px solid var(--border, #ccc);
}
.whatif__btn--primary {
  background: var(--blue);
  color: white;
}
</style>
