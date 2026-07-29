<script setup lang="ts">
import { ref } from 'vue'
import { epcClass } from '../formatters/format'
import { patchWorksEstimate } from '../services/api'
import ProvenanceTree from './ProvenanceTree.vue'

const props = defineProps<{
  affordability: any
  epc: any
  persons?: any
  rid?: string
}>()

function epcStepClass(band: string): string {
    const g = epcClass(band)
    return g ? `epc-step--${g}` : ''
}

// ── Provenance toggle state (one at a time) ────────────
const showProvenance = ref<string | null>(null)
function toggleProvenance(key: string) {
  showProvenance.value = showProvenance.value === key ? null : key
}

function isImpossible(val: any): boolean {
  return val && !val.succeeded && val.error != null
}

// ── Works estimate inline editing ─────────────────────
const editingPerson = ref<string | null>(null)
const editValue = ref<string>('')

function startEdit(person: string, currentValue: number | null) {
  editingPerson.value = person
  editValue.value = currentValue != null ? String(currentValue) : ''
}

function cancelEdit() {
  editingPerson.value = null
  editValue.value = ''
}

async function saveEdit(person: string) {
  editingPerson.value = null
  const parsed = editValue.value === '' ? null : Number(editValue.value)
  if (isNaN(parsed as number) && editValue.value !== '') return
  if (!props.rid) return
  await patchWorksEstimate(props.rid, person, parsed as number | null)
}

function handleKeydown(e: KeyboardEvent, person: string) {
  if (e.key === 'Enter') saveEdit(person)
  else if (e.key === 'Escape') cancelEdit()
}

// ── Helpers for works display ─────────────────────────
const worksEstimates = () =>
  props.affordability?.works_estimates?.succeeded
    ? (props.affordability.works_estimates.value as Record<string, number> ?? {})
    : {}

const personList = () =>
  props.persons?.value
    ? (props.persons.value as Array<Record<string, unknown>>)
    : []

function personRequiresWorks(personName: string): boolean {
  const p = personList().find((x: any) => x.name === personName)
  return p?.works_estimate_required === true
}
</script>

<template>
  <section id="section-costs" class="detail-section">
    <h2 class="detail-section__title">Costs</h2>

    <div class="costs-table">
      <div class="costs-row" :class="{ 'costs-row--impossible': isImpossible(affordability?.monthly_mortgage) }">
        <span class="costs-label">Mortgage</span>
        <span v-if="affordability?.monthly_mortgage?.succeeded && affordability?.monthly_mortgage?.value" class="costs-value">£{{ affordability.monthly_mortgage.value.amount }}</span>
        <span v-else-if="isImpossible(affordability?.monthly_mortgage)" class="costs-value costs-value--impossible">Impossible</span>
        <span v-else class="costs-value">?</span>
        <button
          v-if="affordability?.monthly_mortgage?.provenance"
          class="how-btn"
          :class="{ 'how-btn--active': showProvenance === 'mortgage' }"
          @click="toggleProvenance('mortgage')"
        >{{ showProvenance === 'mortgage' ? 'ⓘ hide' : 'ⓘ how?' }}</button>
      </div>
      <div v-if="showProvenance === 'mortgage' && affordability?.monthly_mortgage?.provenance" class="costs-provenance">
        <ProvenanceTree :provenance="affordability.monthly_mortgage.provenance" />
      </div>

      <div class="costs-row">
        <span class="costs-label">Council Tax</span>
        <span class="costs-value">{{ affordability?.council_tax?.value?.band ?? '?' }} · £{{ affordability?.council_tax?.value?.yearly_cost?.amount ?? '?' }}/yr</span>
        <button
          v-if="affordability?.council_tax?.provenance"
          class="how-btn"
          :class="{ 'how-btn--active': showProvenance === 'council_tax' }"
          @click="toggleProvenance('council_tax')"
        >{{ showProvenance === 'council_tax' ? 'ⓘ hide' : 'ⓘ how?' }}</button>
      </div>
      <div v-if="showProvenance === 'council_tax' && affordability?.council_tax?.provenance" class="costs-provenance">
        <ProvenanceTree :provenance="affordability.council_tax.provenance" />
      </div>

      <div class="costs-row">
        <span class="costs-label">Sinking Fund</span>
        <span class="costs-value">£{{ affordability?.monthly_sinking_fund?.value?.amount ?? '?' }}</span>
        <button
          v-if="affordability?.monthly_sinking_fund?.provenance"
          class="how-btn"
          :class="{ 'how-btn--active': showProvenance === 'sinking_fund' }"
          @click="toggleProvenance('sinking_fund')"
        >{{ showProvenance === 'sinking_fund' ? 'ⓘ hide' : 'ⓘ how?' }}</button>
      </div>
      <div v-if="showProvenance === 'sinking_fund' && affordability?.monthly_sinking_fund?.provenance" class="costs-provenance">
        <ProvenanceTree :provenance="affordability.monthly_sinking_fund.provenance" />
      </div>

      <!-- Cost of Works -->
      <div class="costs-row" :class="{ 'costs-row--impossible': isImpossible(affordability?.total_works) }">
        <span class="costs-label">Cost of Works</span>
        <span v-if="affordability?.total_works?.succeeded && affordability?.total_works?.value" class="costs-value">£{{ affordability.total_works.value.amount }}</span>
        <span v-else-if="isImpossible(affordability?.total_works)" class="costs-value costs-value--impossible">£? — required</span>
        <span v-else class="costs-value">?</span>
        <button
          v-if="affordability?.total_works?.provenance"
          class="how-btn"
          :class="{ 'how-btn--active': showProvenance === 'total_works' }"
          @click="toggleProvenance('total_works')"
        >{{ showProvenance === 'total_works' ? 'ⓘ hide' : 'ⓘ how?' }}</button>
      </div>
      <!-- Per-person works breakdown -->
      <div v-if="affordability?.works_estimates?.succeeded" class="costs-subsection">
        <div
          v-for="p in personList()"
          :key="String(p.name)"
          class="costs-row costs-row--sub"
        >
          <span class="costs-label">{{ p.name }}</span>
          <div v-if="editingPerson === p.name" class="costs-edit-group">
            <span class="costs-edit-prefix">£</span>
            <input
              v-model="editValue"
              type="number"
              class="costs-edit-input"
              autofocus
              @keydown="handleKeydown($event, p.name as string)"
              @blur="saveEdit(p.name as string)"
            />
          </div>
          <span
            v-else-if="p.name in worksEstimates() && worksEstimates()[p.name as string] != null"
            class="costs-value costs-value--clickable"
            @click="startEdit(p.name as string, worksEstimates()[p.name as string])"
          >£{{ worksEstimates()[p.name as string].toLocaleString() }}</span>
          <span
            v-else-if="(p as any).works_estimate_required"
            class="costs-value costs-value--clickable costs-value--required"
            @click="startEdit(p.name as string, null)"
          >£? — required</span>
          <span
            v-else
            class="costs-value costs-value--clickable"
            @click="startEdit(p.name as string, null)"
          >£?</span>
        </div>
      </div>
      <div v-if="showProvenance === 'total_works' && affordability?.total_works?.provenance" class="costs-provenance">
        <ProvenanceTree :provenance="affordability.total_works.provenance" />
      </div>

      <div class="costs-row">
        <span class="costs-label">Commute Cost</span>
        <span class="costs-value">£{{ affordability?.monthly_commute_cost?.value?.yearly_total_gbp != null ? (parseFloat(affordability.monthly_commute_cost.value.yearly_total_gbp ?? '0') / 12).toFixed(2) : '?' }}</span>
        <button
          v-if="affordability?.monthly_commute_cost?.provenance"
          class="how-btn"
          :class="{ 'how-btn--active': showProvenance === 'commute_cost' }"
          @click="toggleProvenance('commute_cost')"
        >{{ showProvenance === 'commute_cost' ? 'ⓘ hide' : 'ⓘ how?' }}</button>
      </div>
      <div v-if="affordability?.monthly_commute_cost?.succeeded && affordability?.monthly_commute_cost?.value?.persons" class="costs-subsection">
        <div v-for="(cost, name) in affordability.monthly_commute_cost.value.persons" :key="name" class="costs-row costs-row--sub">
          <span class="costs-label">{{ name }}</span>
          <span class="costs-value">£{{ (parseFloat(cost.yearly_gbp ?? '0') / 12).toFixed(2) }}/mo</span>
        </div>
      </div>
      <div v-if="showProvenance === 'commute_cost' && affordability?.monthly_commute_cost?.provenance" class="costs-provenance">
        <ProvenanceTree :provenance="affordability.monthly_commute_cost.provenance" />
      </div>

      <!-- Total Monthly -->
      <div class="costs-row costs-row--total" :class="{ 'costs-row--impossible': isImpossible(affordability?.total_monthly_housing_cost) }">
        <span class="costs-label">Total Monthly</span>
        <span v-if="affordability?.total_monthly_housing_cost?.succeeded && affordability?.total_monthly_housing_cost?.value" class="costs-value">£{{ affordability.total_monthly_housing_cost.value.amount }}</span>
        <span v-else-if="isImpossible(affordability?.total_monthly_housing_cost)" class="costs-value costs-value--impossible">Impossible</span>
        <span v-else class="costs-value">?</span>
        <button
          v-if="affordability?.total_monthly_housing_cost?.provenance"
          class="how-btn"
          :class="{ 'how-btn--active': showProvenance === 'total' }"
          @click="toggleProvenance('total')"
        >{{ showProvenance === 'total' ? 'ⓘ hide' : 'ⓘ how?' }}</button>
      </div>
      <div v-if="showProvenance === 'total' && affordability?.total_monthly_housing_cost?.provenance" class="costs-provenance">
        <ProvenanceTree :provenance="affordability.total_monthly_housing_cost.provenance" />
      </div>
    </div>

    <!-- EPC scale -->
    <div v-if="epc?.succeeded" class="epc-section">
      <h3 class="epc-title">EPC Rating</h3>
      <div class="epc-scale">
        <div v-for="band in ['A','B','C','D','E','F','G']" :key="band"
          class="epc-step" :class="epcStepClass(epc.value?.band ?? '')">
          {{ band }}
          <span v-if="(epc.value?.band ?? '').toUpperCase() === band" class="epc-step__marker">▲</span>
        </div>
      </div>
      <div v-if="epc.value?.potential" class="epc-potential">
        Potential: {{ epc.value.potential }}
      </div>
    </div>
  </section>
</template>

<style scoped>
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

.costs-table { display: flex; flex-direction: column; }
.costs-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--sp-2) 0;
  border-bottom: 1px solid var(--border);
}
.costs-row--sub { padding-left: var(--sp-5); border-bottom: none; }
.costs-row--total { font-weight: var(--fw-bold); border-bottom: none; border-top: 2px solid var(--slate-200); margin-top: var(--sp-2); padding-top: var(--sp-3); }
.costs-row--impossible { opacity: 0.5; }
.costs-label { font-size: var(--fs-sm); color: var(--text); }
.costs-value { font-size: var(--fs-sm); font-weight: var(--fw-semibold); margin-left: auto; margin-right: var(--sp-2); }
.costs-value--impossible { color: var(--red); font-style: italic; }
.costs-value--clickable { cursor: pointer; border-bottom: 1px dashed var(--slate-300); }
.costs-value--required { color: var(--red); font-style: italic; }
.costs-subsection { display: flex; flex-direction: column; }
.costs-provenance { background: var(--slate-50); padding: var(--sp-3) var(--sp-4); border-radius: var(--radius); margin: var(--sp-2) 0; }
.costs-edit-group { display: flex; align-items: center; gap: 2px; margin-left: auto; margin-right: var(--sp-2); }
.costs-edit-prefix { font-size: var(--fs-sm); font-weight: var(--fw-semibold); color: var(--text); }
.costs-edit-input {
  width: 100px;
  padding: 2px 6px;
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  border: 1px solid var(--blue);
  border-radius: var(--radius);
  outline: none;
  text-align: right;
}

.how-btn {
  font-size: var(--fs-xs);
  padding: 0.25em 0.5em;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--card-bg);
  color: var(--text-secondary);
  cursor: pointer;
  white-space: nowrap;
}
.how-btn--active { background: var(--slate-100); border-color: var(--blue); color: var(--blue); }

/* EPC */
.epc-section { margin-top: var(--sp-6); }
.epc-title { font-size: var(--fs-sm); font-weight: var(--fw-bold); margin: 0 0 var(--sp-3); color: var(--slate-700); }
.epc-scale { display: flex; gap: 2px; }
.epc-step {
  flex: 1;
  text-align: center;
  padding: var(--sp-2) 0;
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  border-radius: 4px;
  color: var(--slate-500);
  background: var(--slate-100);
  position: relative;
}
.epc-step__marker { position: absolute; bottom: -6px; left: 50%; transform: translateX(-50%); font-size: 12px; }
.epc-step--a { background: var(--green); color: #fff; }
.epc-step--b { background: var(--green-light); }
.epc-step--c { background: var(--yellow-light); }
.epc-step--d { background: var(--yellow); }
.epc-step--e { background: var(--orange); color: #fff; }
.epc-step--f { background: var(--orange-dark); color: #fff; }
.epc-step--g { background: var(--red); color: #fff; }
.epc-potential { font-size: var(--fs-sm); color: var(--text-secondary); margin-top: var(--sp-5); }
</style>
