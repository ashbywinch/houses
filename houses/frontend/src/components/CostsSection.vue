<script setup lang="ts">
import { ref } from 'vue'
import { epcClass } from '../formatters/format'
import ProvenanceTree from './ProvenanceTree.vue'

const props = defineProps<{
  affordability: any
  epc: any
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

// ── Helpers ────────────────────────────────────────────
function attemptValue(val: any): string | null {
  if (!val) return null
  if (val.succeeded && val.value != null) return val.value
  if (!val.succeeded && val.error) return 'Impossible'
  return null
}

function isImpossible(val: any): boolean {
  return val && !val.succeeded && val.error != null
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
      <div v-if="showProvenance === 'total_works' && affordability?.total_works?.provenance && affordability?.works_estimates?.provenance" class="costs-provenance">
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
.costs-subsection { display: flex; flex-direction: column; }
.costs-provenance { background: var(--slate-50); padding: var(--sp-3) var(--sp-4); border-radius: var(--radius); margin: var(--sp-2) 0; }

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
