<script setup lang="ts">
import { ref } from 'vue'
import { commuteDuration, commuteCost, pillColour } from '../formatters/commute'
import { usePropertiesStore } from '../stores/properties'
import ProvenanceView from './ProvenanceView.vue'
import type { Provenance } from '../types'

const store = usePropertiesStore()

const props = defineProps<{
  commutes: any
  currentPerson?: string | null
}>()

/** The colour bands are the person's own thresholds from Settings
 *  (same rule as the house cards), not a global constant. */
function thresholdsFor(key: string): { good: number; fine: number } {
  const person = key.split('/')[0]
  const good = store.commuteGoods[person]
  const fine = store.commuteCeilings[person]?.fine
  if (good != null && fine != null) return { good, fine }
  return { good: 45, fine: 75 }
}

/** A commute's "how is this calculated?" must not list fuel sources the
 *  route doesn't use — the provenance walks every mode branch, so a
 *  train route shows petrol inputs. Drop petrol-labelled sources unless
 *  the winning mode is car/drive. */
function provenanceForMode(p: Provenance, mode?: string): Provenance {
  const isCar = mode === 'car' || mode === 'drive'
  const keep = (label: string): boolean => isCar || !/petrol/i.test(label)
  const walk = (node: Provenance): Provenance | null => {
    if (!keep(node.label)) return null
    const sources: Record<string, Provenance> = {}
    for (const [key, child] of Object.entries(node.sources ?? {})) {
      const filtered = walk(child)
      if (filtered) sources[key] = filtered
    }
    return { ...node, sources }
  }
  return walk(p) ?? p
}

// ── Accordion state ────────────────────────────────────
const expandedCommutes = ref<Set<string>>(new Set())
function toggleCommute(key: string) {
  if (expandedCommutes.value.has(key)) {
    expandedCommutes.value.delete(key)
  } else {
    expandedCommutes.value.add(key)
  }
}

// ── Provenance toggle state (one at a time) ────────────
const showProvenance = ref<string | null>(null)
function toggleProvenance(key: string) {
  showProvenance.value = showProvenance.value === key ? null : key
}
</script>

<template>
  <section id="section-commute" class="detail-section">
    <div class="detail-section__title-row">
      <h2 class="detail-section__title">Commute</h2>
      <router-link
        class="change-destinations"
        :to="'/settings' + (props.currentPerson ? '?person=' + encodeURIComponent(props.currentPerson) : '')"
      >Change destinations →</router-link>
    </div>
    <div class="detail-section__title-row" />
    <div v-for="(c, key) in commutes" :key="key" class="commute-accordion">
      <button class="commute-accordion__header" @click="toggleCommute(key as string)">
        <span class="commute-accordion__label">{{ key }}</span>
        <span
          class="pill"
          :class="pillColour(c, thresholdsFor(String(key)).good, thresholdsFor(String(key)).fine)"
          :title="c?.value?.duration ? undefined : 'No route found for this commute'"
        >
          <template v-if="c?.value?.duration">
            {{ commuteDuration(c?.value?.duration) }}
            {{ commuteCost(c?.value?.daily_cost) }}
          </template>
          <template v-else>No route</template>
        </span>
        <span class="commute-accordion__chevron" :class="{ 'commute-accordion__chevron--open': expandedCommutes.has(key as string) }">▼</span>
      </button>
      <div v-if="expandedCommutes.has(key as string)" class="commute-accordion__body">
        <p v-if="!c?.value?.details?.length && !c?.provenance" class="commute-accordion__empty">
          No route found for this destination — check the address in Settings.
        </p>
        <div v-if="c?.value?.details?.length" class="commute-legs">
          <template v-for="(group, gi) in c.value.details" :key="gi">
            <div v-for="(leg, li) in group.legs" :key="`${gi}-${li}`" class="commute-leg">
              <span class="commute-leg__mode">{{ leg.mode }}</span>
              <span class="commute-leg__duration">{{ leg.duration.value }} min</span>
              <span v-if="li === 0 && group.cost != null" class="commute-leg__cost">
                £{{ (typeof group.cost === 'number' ? group.cost : parseFloat(group.cost?.amount ?? '0')).toFixed(2) }}
              </span>
              <span v-if="li === 0 && group.operator" class="commute-leg__operator">{{ group.operator }}</span>
              <span v-if="leg.end_station" class="commute-leg__destination">{{ leg.end_station }}</span>
            </div>
          </template>
          <div v-if="c?.value?.route_description" class="commute-route">
            {{ c.value.route_description }}
          </div>
        </div>

        <!-- Provenance trigger -->
        <div class="commute-provenance-trigger">
          <button
            class="how-btn"
            :class="{ 'how-btn--active': showProvenance === key }"
            @click="toggleProvenance(key as string)"
          >
            {{ showProvenance === key ? 'ⓘ hide source' : 'ⓘ how?' }}
          </button>
        </div>
        <div v-if="showProvenance === key && c?.provenance" class="commute-provenance-tree">
          <ProvenanceView
            :provenance="provenanceForMode(c.provenance, c?.value?.mode)"
            title="Commute"
          />
        </div>
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

.detail-section__title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
}
.change-destinations {
  color: var(--blue);
  font-size: 0.85rem;
  text-decoration: none;
}
.commute-accordion {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin-bottom: var(--sp-2);
  overflow: hidden;
}
.commute-accordion__header {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  width: 100%;
  padding: var(--sp-3) var(--sp-4);
  border: none;
  background: var(--card-bg);
  cursor: pointer;
  font: inherit;
  text-align: left;
  min-height: 44px;
}
.commute-accordion__header:hover { background: var(--slate-50); }
.commute-accordion__label { font-weight: var(--fw-semibold); font-size: var(--fs-sm); flex: 1; }
.commute-accordion__chevron { font-size: var(--fs-xs); color: var(--text-muted); transition: transform var(--transition); }
.commute-accordion__chevron--open { transform: rotate(180deg); }
.commute-accordion__empty {
  color: var(--text-muted);
  font-size: 0.85rem;
  margin: 0.5rem 0;
}
.commute-accordion__body {
  padding: var(--sp-3) var(--sp-4);
  border-top: 1px solid var(--border);
  background: var(--slate-50);
}

.commute-legs { display: flex; flex-direction: column; gap: var(--sp-1); }
.commute-leg { display: flex; gap: var(--sp-2); font-size: var(--fs-sm); }
.commute-leg__mode { font-weight: var(--fw-semibold); min-width: 56px; color: var(--slate-600); }
.commute-leg__duration { color: var(--slate-700); }
.commute-leg__cost { color: var(--text-secondary); }
.commute-leg__operator { font-size: var(--fs-xs); color: var(--text-muted); font-style: italic; }
.commute-leg__destination { font-size: var(--fs-xs); color: var(--text-muted); }
.commute-route { font-size: var(--fs-xs); color: var(--text-muted); font-style: italic; margin-top: var(--sp-1); }

/* Provenance trigger */
.commute-provenance-trigger { margin-top: var(--sp-2); }
.commute-provenance-tree {
  margin-top: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  background: var(--card-bg);
  border-radius: var(--radius);
  border: 1px solid var(--slate-200);
}

/* "How?" button */
.how-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-xs);
  color: var(--slate-400);
  background: none;
  border: 1px solid var(--slate-200);
  border-radius: var(--radius-sm);
  padding: 2px 8px;
  cursor: pointer;
  transition: all var(--transition);
  font-family: var(--font);
}
.how-btn:hover { background: var(--slate-100); color: var(--slate-600); border-color: var(--slate-300); }
.how-btn--active { background: var(--blue-bg); color: var(--blue-text); border-color: var(--blue); }

/* Pill */
.pill {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: var(--radius-full);
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  line-height: 1.6;
  white-space: nowrap;
}
.pill--good { background: var(--green); color: #fff; }
.pill--warn { background: var(--orange); color: #fff; }
.pill--bad { background: var(--red); color: #fff; }
.pill--muted { background: var(--commute-none); color: #fff; }
</style>
