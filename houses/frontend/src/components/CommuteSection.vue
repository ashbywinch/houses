<script setup lang="ts">
import { ref } from 'vue'
import { commuteDuration, commuteCost, pillColour } from '../formatters/commute'
import ProvenanceTree from './ProvenanceTree.vue'

defineProps<{
  commutes: any
  goodThreshold?: number
  warnThreshold?: number
}>()

// ── Accordion state ────────────────────────────────────
const expandedCommutes = ref<Set<string>>(new Set())
function toggleCommute(key: string) {
  if (expandedCommutes.value.has(key)) {
    expandedCommutes.value.delete(key)
  } else {
    expandedCommutes.value.add(key)
  }
}
</script>

<template>
  <section id="section-commute" class="detail-section">
    <h2 class="detail-section__title">Commute</h2>
    <div v-for="(c, key) in commutes" :key="key" class="commute-accordion">
      <button class="commute-accordion__header" @click="toggleCommute(key as string)">
        <span class="commute-accordion__label">{{ key }}</span>
        <span class="pill" :class="pillColour(c, goodThreshold ?? 45, warnThreshold ?? 75)">
          {{ commuteDuration(c?.value?.duration) }}
          {{ commuteCost(c?.value?.daily_cost) }}
        </span>
        <span class="commute-accordion__chevron" :class="{ 'commute-accordion__chevron--open': expandedCommutes.has(key as string) }">▼</span>
      </button>
      <div v-if="expandedCommutes.has(key as string)" class="commute-accordion__body">
        <div v-if="c?.value?.details?.length" class="commute-legs">
          <template v-for="(group, gi) in c.value.details" :key="gi">
            <div v-for="(leg, li) in group.legs" :key="`${gi}-${li}`" class="commute-leg">
              <span class="commute-leg__mode">{{ leg.mode }}</span>
              <span class="commute-leg__duration">{{ leg.duration_minutes }} min</span>
              <span v-if="li === 0 && group.cost != null" class="commute-leg__cost">
                £{{ (typeof group.cost === 'number' ? group.cost : group.cost?.amount).toFixed(2) }}
              </span>
              <span v-if="li === 0 && group.operator" class="commute-leg__operator">{{ group.operator }}</span>
              <span v-if="leg.end_station" class="commute-leg__destination">{{ leg.end_station }}</span>
            </div>
          </template>
          <div v-if="c?.value?.route_description" class="commute-route">
            {{ c.value.route_description }}
          </div>
        </div>
        <div class="commute-provenance">
          <ProvenanceTree v-if="c?.provenance" :provenance="c.provenance" />
          <span v-else>unknown</span>
        </div>
      </div>
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
.commute-accordion { border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; overflow: hidden; }
.commute-accordion__header {
  display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 10px 12px; border: none; background: var(--card-bg);
  cursor: pointer; font: inherit; text-align: left;
  min-height: 44px;
}
.commute-accordion__label { font-weight: 600; font-size: 14px; flex: 1; }
.commute-accordion__chevron { font-size: 10px; color: var(--text-muted); transition: transform 0.2s; }
.commute-accordion__chevron--open { transform: rotate(180deg); }
.commute-accordion__body { padding: 8px 12px 12px; border-top: 1px solid var(--border); background: #fafafa; }
.commute-legs { display: flex; flex-direction: column; gap: 4px; }
.commute-leg { display: flex; gap: 8px; font-size: 13px; }
.commute-leg__mode { font-weight: 600; min-width: 60px; }
.commute-leg__duration { color: var(--text-secondary); }
.commute-leg__cost { color: var(--text-secondary); }
.commute-leg__operator { font-size: 12px; color: var(--text-muted); font-style: italic; }
.commute-leg__destination { font-size: 12px; color: var(--text-muted); }
.commute-route { font-size: 12px; color: var(--text-muted); font-style: italic; margin-top: 4px; }
.commute-provenance { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
.pill {
  display: inline-flex; align-items: center; padding: 2px 10px;
  border-radius: 999px; font-size: 12px; font-weight: 700;
  line-height: 1.6; white-space: nowrap;
}
.pill--good { background: var(--green-bg); color: var(--green); }
.pill--warn { background: var(--orange-bg); color: var(--orange); }
.pill--bad { background: var(--red-bg); color: var(--red); }
.pill--muted { background: var(--muted-bg); color: var(--muted); }
</style>
