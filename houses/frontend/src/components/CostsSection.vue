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
</script>

<template>
  <section id="section-costs" class="detail-section">
    <h2 class="detail-section__title">Costs</h2>

    <div class="costs-table">
      <div class="costs-row">
        <span class="costs-label">Mortgage</span>
        <span class="costs-value">£{{ affordability?.monthly_mortgage?.value?.amount ?? '?' }}</span>
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
        <span class="costs-value">{{ affordability?.council_tax?.value?.band ?? '?' }} · £{{ affordability?.council_tax?.value?.yearly_cost ?? '?' }}/yr</span>
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

      <div class="costs-row">
        <span class="costs-label">Commute Cost</span>
        <span class="costs-value">£{{ affordability?.monthly_commute_cost?.value?.yearly_total_gbp != null ? (affordability.monthly_commute_cost.value.yearly_total_gbp / 12).toFixed(2) : '?' }}</span>
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
          <span class="costs-value">£{{ (cost.yearly_gbp / 12).toFixed(2) }}/mo</span>
        </div>
      </div>
      <div v-if="showProvenance === 'commute_cost' && affordability?.monthly_commute_cost?.provenance" class="costs-provenance">
        <ProvenanceTree :provenance="affordability.monthly_commute_cost.provenance" />
      </div>

      <div class="costs-row costs-row--total">
        <span class="costs-label">Total Monthly</span>
        <span class="costs-value">£{{ affordability?.total_monthly_housing_cost?.value?.amount ?? '?' }}</span>
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
  border-bottom: 1px solid var(--divider);
  font-size: var(--fs-sm);
  gap: var(--sp-2);
}
.costs-row:last-child { border-bottom: none; }
.costs-row--sub { padding-left: var(--sp-4); font-size: var(--fs-xs); color: var(--text-secondary); }
.costs-row--total {
  font-weight: var(--fw-bold);
  font-size: var(--fs-base);
  border-top: 2px solid var(--blue);
  margin-top: var(--sp-1);
  padding-top: var(--sp-3);
}
.costs-row--total .costs-value { color: var(--blue); }
.costs-label { color: var(--text-secondary); }
.costs-value { font-weight: var(--fw-semibold); white-space: nowrap; }

/* Provenance inline */
.costs-provenance {
  padding: var(--sp-2) var(--sp-3);
  background: var(--slate-50);
  border-radius: var(--radius);
  border: 1px solid var(--slate-200);
  margin-bottom: var(--sp-1);
}

/* "How?" button */
.how-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  color: var(--slate-400);
  background: none;
  border: 1px solid var(--slate-200);
  border-radius: var(--radius-sm);
  padding: 2px 8px;
  cursor: pointer;
  transition: all var(--transition);
  font-family: var(--font);
  flex-shrink: 0;
}
.how-btn:hover { background: var(--slate-100); color: var(--slate-600); border-color: var(--slate-300); }
.how-btn--active { background: var(--blue-bg); color: var(--blue-text); border-color: var(--blue); }

/* Stamp duty — removed per design (in provenance chain) */

.epc-section { margin-top: var(--sp-4); }
.epc-title { font-size: var(--fs-sm); font-weight: var(--fw-semibold); margin: 0 0 var(--sp-2); }
.epc-scale { display: flex; gap: var(--sp-1); }
.epc-step {
  flex: 1; text-align: center; padding: var(--sp-2) var(--sp-1);
  border-radius: var(--radius-sm); font-size: var(--fs-sm); font-weight: var(--fw-bold); color: #fff;
  position: relative;
}
.epc-step--a { background: #2e7d32; }
.epc-step--bc { background: #1565c0; }
.epc-step--d { background: var(--amber); color: #1a1a1a; }
.epc-step--e { background: #e65100; }
.epc-step--fg { background: #c62828; }
.epc-step__marker { position: absolute; bottom: -16px; left: 50%; transform: translateX(-50%); font-size: var(--fs-xs); color: var(--slate-800); }
.epc-potential { font-size: var(--fs-sm); color: var(--text-secondary); margin-top: var(--sp-5); }
</style>
