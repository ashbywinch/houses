<script setup lang="ts">
import { epcClass } from '../formatters/format'

defineProps<{
  affordability: any
  epc: any
}>()

function epcStepClass(band: string): string {
    const g = epcClass(band)
    return g ? `epc-step--${g}` : ''
}
</script>

<template>
  <section id="section-costs" class="detail-section">
    <h2 class="detail-section__title">Costs</h2>

    <div class="costs-table">
      <div class="costs-row">
        <span class="costs-label">Mortgage</span>
        <span class="costs-value">£{{ affordability?.monthly_mortgage?.value?.amount ?? '?' }}</span>
      </div>
      <div class="costs-row">
        <span class="costs-label">Council Tax</span>
        <span class="costs-value">{{ affordability?.council_tax?.value?.band ?? '?' }} · £{{ affordability?.council_tax?.value?.yearly_cost ?? '?' }}/yr</span>
      </div>
      <div class="costs-row">
        <span class="costs-label">Sinking Fund</span>
        <span class="costs-value">£{{ affordability?.monthly_sinking_fund?.value ?? '?' }}</span>
      </div>
      <div class="costs-row">
        <span class="costs-label">Commute Cost</span>
        <span class="costs-value">£{{ affordability?.monthly_commute_cost?.value?.yearly_total_gbp != null ? (affordability.monthly_commute_cost.value.yearly_total_gbp / 12).toFixed(2) : '?' }}</span>
      </div>
      <div v-if="affordability?.monthly_commute_cost?.succeeded && affordability?.monthly_commute_cost?.value?.persons" class="costs-subsection">
        <div v-for="(cost, name) in affordability.monthly_commute_cost.value.persons" :key="name" class="costs-row costs-row--sub">
          <span class="costs-label">{{ name }}</span>
          <span class="costs-value">£{{ (cost.yearly_gbp / 12).toFixed(2) }}/mo</span>
        </div>
      </div>
      <div class="costs-row costs-row--total">
        <span class="costs-label">Total Monthly</span>
        <span class="costs-value">£{{ affordability?.total_monthly_housing_cost?.value?.amount ?? '?' }}</span>
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

    <!-- Stamp duty -->
    <div v-if="affordability?.stamp_duty" class="detail-field">
      <span class="detail-field__label">Stamp Duty</span>
      <span class="detail-field__value">£{{ affordability.stamp_duty?.succeeded ? affordability.stamp_duty.value?.amount?.toLocaleString() : '?' }}</span>
    </div>
  </section>
</template>

<style scoped>
.detail-section {
  padding: 16px;
  border-bottom: 8px solid var(--page-bg);
}
.detail-section__title {
  font-size: 16px; font-weight: 700; margin: 0 0 12px;
}
.costs-table { display: flex; flex-direction: column; }
.costs-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 0; border-bottom: 1px solid var(--divider);
  font-size: 14px;
}
.costs-row--sub { padding-left: 16px; font-size: 13px; color: var(--text-secondary); }
.costs-row--total { font-weight: 700; background: var(--blue-bg); margin: 0 -16px; padding: 10px 16px; border-bottom: none; border-radius: 8px; }
.costs-label { color: var(--text-secondary); }
.costs-value { font-weight: 600; }
.epc-section { margin-top: 16px; }
.epc-title { font-size: 14px; font-weight: 600; margin: 0 0 8px; }
.epc-scale { display: flex; gap: 4px; }
.epc-step {
  flex: 1; text-align: center; padding: 8px 4px;
  border-radius: 6px; font-size: 14px; font-weight: 700; color: #fff;
  position: relative;
}
.epc-step--a { background: #2e7d32; }
.epc-step--bc { background: #1565c0; }
.epc-step--d { background: #f9a825; color: #1a1a1a; }
.epc-step--e { background: #e65100; }
.epc-step--fg { background: #c62828; }
.epc-step__marker { position: absolute; bottom: -16px; left: 50%; transform: translateX(-50%); font-size: 12px; color: #1a1a1a; }
.epc-potential { font-size: 13px; color: var(--text-secondary); margin-top: 20px; }
.detail-field {
  display: flex; flex-wrap: wrap; align-items: baseline;
  gap: 8px; padding: 6px 0;
}
.detail-field__label { font-size: 13px; font-weight: 600; color: var(--text-secondary); min-width: 80px; }
.detail-field__value { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 14px; }
</style>
