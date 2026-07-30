<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Provenance } from '../types'

const props = withDefaults(defineProps<{
  provenance: Provenance
  title?: string
  detailLevel?: 'summary' | 'story' | 'detail'
}>(), {
  title: 'Result',
  detailLevel: 'summary',
})

const emit = defineEmits<{
  'update:detailLevel': [level: 'summary' | 'story' | 'detail']
}>()

const activeLevel = ref(props.detailLevel)

function setLevel(level: 'summary' | 'story' | 'detail') {
  activeLevel.value = level
  emit('update:detailLevel', level)
}

// ── Helpers ──

type FlatNode = {
  label: string
  desc: string
  sourceType: string
  freshness: string
  url: string
  indent: number
  isRef: boolean
  refId: string
}

function daysSince(dateStr: string): number | null {
  if (!dateStr || dateStr === '—') return null
  const d = Date.now() - new Date(dateStr).getTime()
  return d / (1000 * 60 * 60 * 24)
}

const SOURCE_LABELS: Record<string, string> = {
  api: 'API', calc: 'Calculation', user: 'Your input',
  config: 'Config', geocode: 'Geocode', db: 'Database',
}

const SOURCE_COLORS: Record<string, string> = {
  api: 'var(--orange)', calc: 'var(--purple)', user: 'var(--blue)',
  config: 'var(--amber)', geocode: 'var(--green)', db: 'var(--slate-500)',
}

const SOURCE_ICONS: Record<string, string> = {
  api: '🌐', calc: '🔢', user: '✏️',
  config: '⚙️', geocode: '📍', db: '🗄️',
}

function sourceLabel(st: string | undefined): string {
  return SOURCE_LABELS[st ?? ''] ?? 'Source'
}

function freshnessLabel(days: number | null): { text: string; cls: string } {
  if (days === null) return { text: '—', cls: 'unknown' }
  if (days <= 1) return { text: 'Updated today', cls: 'fresh' }
  if (days <= 7) return { text: `Updated ${Math.round(days)} day${Math.round(days) === 1 ? '' : 's'} ago`, cls: 'fresh' }
  if (days <= 30) return { text: `Updated ${Math.round(days)} days ago`, cls: 'aging' }
  return { text: `Updated ${Math.round(days)} days ago`, cls: 'stale' }
}

function trustLevel(p: Provenance): 'fresh' | 'aging' | 'stale' {
  let oldest = 0
  function walk(n: Provenance) {
    if (n.freshness && n.freshness !== '—') {
      const d = daysSince(n.freshness) ?? 0
      if (d > oldest) oldest = d
    }
    if (n.sources) Object.values(n.sources).forEach(walk)
  }
  walk(p)
  if (oldest <= 7) return 'fresh'
  if (oldest <= 30) return 'aging'
  return 'stale'
}

function countByType(p: Provenance, type: string): number {
  let count = 0
  function walk(n: Provenance) {
    if (n.sourceType === type) count++
    if (n.sources) Object.values(n.sources).forEach(walk)
  }
  walk(p)
  return count
}

function totalSourceCount(p: Provenance): number {
  const api = countByType(p, 'api')
  const user = countByType(p, 'user')
  const config = countByType(p, 'config')
  const geocode = countByType(p, 'geocode')
  const db = countByType(p, 'db')
  return api + user + config + geocode + db
}

function calcCount(p: Provenance): number {
  return countByType(p, 'calc')
}

// ── Node type helpers (for story view sections) ──

function hasNodesOfType(p: Provenance, type: string): boolean {
  if (p.sourceType === type) return true
  if (p.sources) return Object.values(p.sources).some(s => hasNodesOfType(s, type))
  return false
}

function filterNodesByType(p: Provenance, type: string): Record<string, Provenance> {
  const result: Record<string, Provenance> = {}
  if (!p.sources) return result
  for (const [key, src] of Object.entries(p.sources)) {
    if (src.sourceType === type) {
      result[key] = src
    }
  }
  return result
}

// ── Deduplication ──

function findSharedRefs(p: Provenance): Map<string, Provenance> {
  const seen = new Map<string, { count: number; node: Provenance }>()
  function walk(n: Provenance, path: string) {
    const key = `${n.label}::${n.sourceType ?? ''}`
    if (seen.has(key)) {
      seen.get(key)!.count++
    } else {
      seen.set(key, { count: 1, node: n })
    }
    if (n.sources) Object.values(n.sources).forEach(s => walk(s, path + '/' + n.label))
  }
  walk(p, '')
  const shared = new Map<string, Provenance>()
  for (const [key, val] of seen) {
    if (val.count > 1) shared.set(key, val.node)
  }
  return shared
}

function buildFlattenedTree(p: Provenance, refs: Map<string, Provenance>): FlatNode[] {
  const result: FlatNode[] = []
  function walk(n: Provenance, indent: number) {
    const key = `${n.label}::${n.sourceType ?? ''}`
    const isRef = refs.has(key)
    result.push({
      label: n.label,
      desc: n.description ?? '',
      sourceType: n.sourceType ?? 'unknown',
      freshness: n.freshness ?? '',
      url: n.url ?? '',
      indent,
      isRef,
      refId: key,
    })
    if (n.sources && !(isRef && indent > 0)) {
      Object.values(n.sources).forEach(s => walk(s, indent + 1))
    }
  }
  walk(p, 0)
  return result
}

// ── Plain language labels ──

const HUMAN_LABELS: Record<string, string> = {
  'total_monthly_cost': 'Your total monthly housing cost',
  'mortgage_required': 'The amount you still need to borrow',
  'monthly_mortgage': 'Your monthly mortgage payment',
  'stamp_duty': 'Property purchase tax (Stamp Duty)',
  'total_works': 'Estimated renovation costs',
  'total_equity': 'Your available funds (equity + savings)',
  'life_insurance_total': 'Life insurance costs',
  'yearly_sinking_fund': 'Annual property maintenance fund',
  'commute_breakdown': 'How we calculated your commute costs',
  'rental_income': 'Rental income (subtracted)',
  'financial_settings': 'Your financial preferences',
  'persons_config': 'Your household details',
  'comment_status': 'Your current home situation',
  'works_estimates': 'Renovation estimates you entered',
  'rightmove_price': 'Property price from Rightmove',
  'rightmove_address': 'Property address from Rightmove',
  'rightmove_bedrooms': 'Bedroom count from Rightmove',
  'best_address': 'Best available address',
  'best_location': 'Property location',
  'user_entered_address': 'Address you entered',
  'corrected_address': 'Corrected address',
  'postcode': 'Postcode',
  'epc': 'Energy Performance Certificate',
  'council_tax': 'Council Tax band',
  'walkability': 'Walkability score',
  'town_name': 'Town name',
  'town_desc': 'Area description',
  'primary_school': 'Primary school',
  'secondary_school': 'Secondary school',
  'transit_result': 'Public transport route',
  'rail_fare': 'Train fare',
  'geocode': 'Location lookup (geocoding)',
  'precise_location': 'Pinpointed location',
  'rightmove_location': 'Rightmove location',
  'triage_status': 'Your assessment status',
  'favourite': 'Favourite flag',
  'dismissed': 'Dismissed flag',
  'is_viewed': 'Viewed flag',
  'user_notes': 'Your notes',
}

function humanLabel(label: string): string {
  return HUMAN_LABELS[label] ?? label
}

function extractHostname(url: string): string {
  try { return new URL(url).hostname }
  catch { return url }
}

function getValue(p: Provenance): unknown {
  return (p as any).value
}

// ── Computed ──

const sharedRefs = computed(() => findSharedRefs(props.provenance))
const flatTree = computed(() => buildFlattenedTree(props.provenance, sharedRefs.value))
const trust = computed(() => trustLevel(props.provenance))
const totalSources = computed(() => totalSourceCount(props.provenance))
const totalCalcs = computed(() => calcCount(props.provenance))
const hasSources = computed(() => !!(props.provenance.sources && Object.keys(props.provenance.sources).length > 0))
const rootFreshness = computed(() => props.provenance.freshness ?? null)
const rootFreshnessLabel = computed(() => freshnessLabel(rootFreshness.value !== null ? daysSince(rootFreshness.value) : null))

const hasUserInputs = computed(() => hasNodesOfType(props.provenance, 'user'))
const hasApiNodes = computed(() => hasNodesOfType(props.provenance, 'api'))
const hasCalcNodes = computed(() => hasNodesOfType(props.provenance, 'calc'))
const userInputNodes = computed(() => filterNodesByType(props.provenance, 'user'))
const apiNodes = computed(() => filterNodesByType(props.provenance, 'api'))
const calcNodes = computed(() => filterNodesByType(props.provenance, 'calc'))

const legendItems = computed(() => {
  const types = new Set<string>()
  function walk(n: Provenance) {
    if (n.sourceType) types.add(n.sourceType)
    if (n.sources) Object.values(n.sources).forEach(walk)
  }
  walk(props.provenance)
  return Array.from(types).map(st => ({
    type: st,
    label: SOURCE_LABELS[st] ?? st,
    color: SOURCE_COLORS[st] ?? 'var(--slate-300)',
  }))
})

const sharedRefsList = computed(() => {
  const list: Array<{ id: string; label: string; freshness: string; color: string }> = []
  for (const [key, node] of sharedRefs.value) {
    list.push({
      id: key,
      label: humanLabel(node.label),
      freshness: node.freshness ?? '',
      color: SOURCE_COLORS[node.sourceType ?? ''] ?? 'var(--slate-300)',
    })
  }
  return list
})
</script>

<template>
  <div class="prov-view" role="region" aria-label="Data provenance">
    <!-- ═══ Trust Bar ═══ -->
    <div class="trust-bar">
      <div class="trust-bar__result">
        <span class="trust-bar__label">{{ title }}</span>
        <span v-if="getValue(provenance) !== undefined && getValue(provenance) !== null" class="trust-bar__value">
          {{ getValue(provenance) }}
        </span>
      </div>
      <div class="trust-bar__meta">
        <span
          class="trust-indicator"
          :class="`trust-indicator--${trust}`"
          :aria-label="`Data is ${trust}`"
        >
          <span class="trust-indicator__dot"></span>
          {{ rootFreshnessLabel.text }}
        </span>
        <span class="trust-bar__sources">
          {{ totalSources }} data source{{ totalSources !== 1 ? 's' : '' }}{{ totalCalcs > 0 ? `, ${totalCalcs} calculation${totalCalcs !== 1 ? 's' : ''}` : '' }}
        </span>
      </div>
      <div v-if="provenance.description" class="trust-bar__explanation">
        {{ provenance.description }}
      </div>
    </div>

    <!-- ═══ View Toggle ═══ -->
    <div class="prov-view-toggle" role="tablist" aria-label="Detail level">
      <button
        class="prov-view-toggle__btn"
        role="tab"
        :aria-selected="activeLevel === 'summary'"
        :tabindex="activeLevel === 'summary' ? 0 : -1"
        @click="setLevel('summary')"
      >Summary</button>
      <button
        v-if="hasSources"
        class="prov-view-toggle__btn"
        role="tab"
        :aria-selected="activeLevel === 'story'"
        :tabindex="activeLevel === 'story' ? 0 : -1"
        @click="setLevel('story')"
      >How we got this</button>
      <button
        v-if="hasSources"
        class="prov-view-toggle__btn"
        role="tab"
        :aria-selected="activeLevel === 'detail'"
        :tabindex="activeLevel === 'detail' ? 0 : -1"
        @click="setLevel('detail')"
      >Full detail</button>
    </div>

    <!-- ═══ Summary View ═══ -->
    <div v-show="activeLevel === 'summary'" class="summary-view">
      <div class="summary-view__narrative">
        <template v-if="provenance.description">
          {{ provenance.description }}
        </template>
        <template v-else>
          This value was determined from {{ totalSources }} data source{{ totalSources !== 1 ? 's' : '' }}
          <template v-if="totalCalcs > 0"> and {{ totalCalcs }} calculation{{ totalCalcs !== 1 ? 's' : '' }}</template>.
        </template>
      </div>
      <div v-if="hasSources" class="summary-view__sources">
        <span
          v-for="(src, key) in provenance.sources"
          :key="key"
          class="summary-view__source-chip"
        >
          <span class="dot" :style="{ background: SOURCE_COLORS[src.sourceType ?? ''] ?? 'var(--slate-300)' }"></span>
          {{ humanLabel(src.label) }}
        </span>
      </div>
    </div>

    <!-- ═══ Story Flow View ═══ -->
    <div v-show="activeLevel === 'story'" class="story-flow">
      <!-- Section 1: Inputs -->
      <div v-if="hasUserInputs" class="story-flow__section">
        <div class="story-flow__heading">
          <span>1. What we started with</span>
          <span class="story-flow__heading-line"></span>
        </div>
        <div class="flow-cards">
          <div
            v-for="(src, key) in userInputNodes"
            :key="key"
            class="flow-card"
          >
            <div class="flow-card__icon" :class="`flow-card__icon--${src.sourceType ?? 'unknown'}`" aria-hidden="true">
              {{ SOURCE_ICONS[src.sourceType ?? ''] ?? '📄' }}
            </div>
            <div class="flow-card__body">
              <div class="flow-card__title">{{ humanLabel(src.label) }}</div>
              <div class="flow-card__desc">{{ src.description || `${sourceLabel(src.sourceType)} data` }}</div>
              <a
                v-if="src.url"
                :href="src.url"
                target="_blank"
                class="flow-card__link"
                rel="noopener"
              >Visit source <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a>
            </div>
            <span
              v-if="src.freshness && src.freshness !== '—'"
              class="flow-card__freshness"
              :class="`flow-card__freshness--${freshnessLabel(daysSince(src.freshness)).cls}`"
            >{{ freshnessLabel(daysSince(src.freshness)).text }}</span>
          </div>
        </div>
      </div>

      <!-- Connector arrow -->
      <div v-if="hasUserInputs && hasApiNodes" class="flow-connector" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M19 12l-7 7-7-7"/></svg>
      </div>

      <!-- Section 2: API lookups -->
      <div v-if="hasApiNodes" class="story-flow__section">
        <div class="story-flow__heading">
          <span>{{ hasUserInputs ? '2. Where we looked it up' : '1. Where the data comes from' }}</span>
          <span class="story-flow__heading-line"></span>
        </div>
        <div class="flow-cards">
          <div
            v-for="(src, key) in apiNodes"
            :key="key"
            class="flow-card"
          >
            <div class="flow-card__icon flow-card__icon--api" aria-hidden="true">🌐</div>
            <div class="flow-card__body">
              <div class="flow-card__title">{{ humanLabel(src.label) }}</div>
              <div class="flow-card__desc">{{ src.description || 'Looked up from an external data service' }}</div>
              <a
                v-if="src.url"
                :href="src.url"
                target="_blank"
                class="flow-card__link"
                rel="noopener"
              >{{ extractHostname(src.url) }} <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a>
            </div>
            <span
              v-if="src.freshness && src.freshness !== '—'"
              class="flow-card__freshness"
              :class="`flow-card__freshness--${freshnessLabel(daysSince(src.freshness)).cls}`"
            >{{ freshnessLabel(daysSince(src.freshness)).text }}</span>
          </div>
        </div>
      </div>

      <!-- Connector arrow -->
      <div v-if="hasCalcNodes" class="flow-connector" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M19 12l-7 7-7-7"/></svg>
      </div>

      <!-- Section 3: Calculations -->
      <div v-if="hasCalcNodes" class="story-flow__section">
        <div class="story-flow__heading">
          <span>{{ hasApiNodes || hasUserInputs ? '3. How we calculated it' : '1. How we calculated it' }}</span>
          <span class="story-flow__heading-line"></span>
        </div>
        <div class="flow-cards">
          <div
            v-for="(calc, key) in calcNodes"
            :key="key"
            class="flow-card"
          >
            <div class="flow-card__icon flow-card__icon--calc" aria-hidden="true">🔢</div>
            <div class="flow-card__body">
              <div class="flow-card__title">{{ humanLabel(calc.label) }}</div>
              <div class="flow-card__desc">{{ calc.description || 'A calculation based on the data above' }}</div>
            </div>
          </div>
        </div>

        <!-- Formula explanation -->
        <div v-if="provenance.formula" class="formula-explain">
          <div class="formula-explain__title">
            <span>How the math works</span>
          </div>
          <div class="formula-explain__text">
            We started with the inputs above and combined them step by step.
          </div>
          <div class="formula-explain__steps">
            <div
              v-for="(line, li) in provenance.formula.lines"
              :key="li"
              class="formula-explain__step"
            >
              <span class="formula-explain__step-num">{{ li + 1 }}</span>
              <span class="formula-explain__step-label">{{ line.label }}</span>
              <span class="formula-explain__step-value">{{ line.value }}</span>
            </div>
          </div>
          <div class="formula-explain__result">
            <span>Result</span>
            <span class="formula-explain__result-value">{{ provenance.formula.result }}</span>
          </div>
        </div>
      </div>

      <!-- Result card -->
      <div v-if="hasSources" class="flow-connector" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M19 12l-7 7-7-7"/></svg>
      </div>

      <div class="story-flow__section">
        <div class="story-flow__heading">
          <span>{{ hasCalcNodes ? '4.' : hasApiNodes ? '3.' : '2.' }} The result</span>
          <span class="story-flow__heading-line"></span>
        </div>
        <div class="flow-cards">
          <div class="flow-card flow-card--result">
            <div class="flow-card__icon flow-card__icon--result" aria-hidden="true">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
            </div>
            <div class="flow-card__body">
              <div class="flow-card__title">{{ humanLabel(provenance.label) }}</div>
              <div class="flow-card__desc">{{ provenance.description || 'The final value after combining all the data and calculations above.' }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ Full Detail View ═══ -->
    <div v-show="activeLevel === 'detail'" class="detail-view">
      <!-- Flattened tree -->
      <div class="detail-tree" role="tree" :aria-label="`${title} provenance detail`">
        <div
          v-for="(node, i) in flatTree"
          :key="i"
          class="detail-node"
          :class="`detail-node--indent-${node.indent}`"
          role="treeitem"
        >
          <span class="detail-node__dot" :style="{ background: SOURCE_COLORS[node.sourceType] ?? 'var(--slate-300)' }"></span>
          <span class="detail-node__label">{{ humanLabel(node.label) }}</span>
          <span v-if="node.desc" class="detail-node__desc">— {{ node.desc }}</span>
          <span v-if="node.isRef && node.indent > 0" class="detail-node__ref">📍 Shared</span>
          <a
            v-if="node.url"
            :href="node.url"
            target="_blank"
            class="detail-node__link"
            rel="noopener"
            title="Open source"
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
          </a>
          <span v-if="node.freshness" class="detail-node__freshness">{{ freshnessLabel(daysSince(node.freshness)).text }}</span>
        </div>
      </div>

      <!-- Shared Reference Library -->
      <div v-if="sharedRefsList.length > 0" class="shared-refs">
        <div class="shared-refs__title">Data sources used in multiple places</div>
        <div class="shared-refs__grid">
          <div
            v-for="ref in sharedRefsList"
            :key="ref.id"
            class="shared-ref"
            tabindex="0"
            role="button"
          >
            <span class="shared-ref__icon" :style="{ background: ref.color }"></span>
            <span class="shared-ref__label">{{ ref.label }}</span>
            <span v-if="ref.freshness" class="shared-ref__freshness">{{ freshnessLabel(daysSince(ref.freshness)).text }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ Legend ═══ -->
    <div class="legend">
      <span v-for="item in legendItems" :key="item.type" class="legend__item">
        <span class="legend__dot" :style="{ background: item.color }"></span>
        {{ item.label }}
      </span>
    </div>
  </div>
</template>

<style scoped>
/* ══════════════════════════════════════════
   Provenance View — trust bar
   ══════════════════════════════════════════ */
.prov-view {
  font-size: var(--fs-sm);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: var(--card-bg);
  box-shadow: var(--shadow-sm);
}

/* ── Trust Bar ── */
.trust-bar {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  padding: var(--sp-4) var(--sp-5);
  border-bottom: 1px solid var(--border);
  background: var(--slate-50);
  flex-wrap: wrap;
}
.trust-bar__result {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.trust-bar__label {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-weight: var(--fw-semibold);
}
.trust-bar__value {
  font-size: var(--fs-lg);
  font-weight: var(--fw-bold);
  color: var(--slate-900);
  line-height: var(--lh-tight);
}
.trust-bar__meta {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  margin-left: auto;
  flex-wrap: wrap;
}
.trust-indicator {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  font-size: var(--fs-xs);
  font-weight: var(--fw-medium);
  padding: var(--sp-1) var(--sp-3);
  border-radius: var(--radius-full);
  white-space: nowrap;
}
.trust-indicator--fresh { background: var(--green-bg); color: var(--green-text); }
.trust-indicator--aging { background: var(--amber-bg); color: var(--amber-text); }
.trust-indicator--stale { background: var(--red-bg); color: var(--red-text); }
.trust-indicator__dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: currentColor;
  flex-shrink: 0;
}
.trust-bar__sources {
  font-size: var(--fs-xs);
  color: var(--text-muted);
}
.trust-bar__explanation {
  width: 100%;
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  line-height: var(--lh);
  margin-top: var(--sp-1);
}

/* ── View Toggle ── */
.prov-view-toggle {
  display: flex;
  gap: 0;
  padding: 0 var(--sp-5);
  border-bottom: 1px solid var(--border);
  background: var(--card-bg);
}
.prov-view-toggle__btn {
  padding: var(--sp-2) var(--sp-4);
  font-size: var(--fs-xs);
  font-weight: var(--fw-medium);
  color: var(--text-muted);
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: all var(--transition);
  min-height: 36px;
  display: flex;
  align-items: center;
  font-family: var(--font);
  cursor: pointer;
  background: none;
  border-top: none;
  border-left: none;
  border-right: none;
}
.prov-view-toggle__btn:hover { color: var(--text-secondary); }
.prov-view-toggle__btn[aria-selected="true"] {
  color: var(--blue);
  border-bottom-color: var(--blue);
}

/* ── Summary View ── */
.summary-view {
  padding: var(--sp-5);
}
.summary-view__narrative {
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  line-height: var(--lh-loose);
  max-width: 600px;
}
.summary-view__sources {
  display: flex;
  gap: var(--sp-3);
  margin-top: var(--sp-4);
  flex-wrap: wrap;
}
.summary-view__source-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  background: var(--slate-50);
  border: 1px solid var(--slate-200);
  border-radius: var(--radius-full);
  font-size: var(--fs-xs);
  font-weight: var(--fw-medium);
  color: var(--slate-600);
}
.summary-view__source-chip .dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
}

/* ── Story Flow ── */
.story-flow {
  padding: var(--sp-5);
}
.story-flow__section {
  margin-bottom: var(--sp-5);
}
.story-flow__section:last-child { margin-bottom: 0; }
.story-flow__heading {
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: var(--sp-3);
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.story-flow__heading-line {
  flex: 1;
  height: 1px;
  background: var(--slate-200);
}

/* Flow Cards */
.flow-cards {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.flow-card {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-4);
  padding: var(--sp-4);
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  transition: box-shadow var(--transition);
}
.flow-card:hover { box-shadow: var(--shadow-sm); }
.flow-card--result {
  border-color: var(--slate-300);
  background: var(--slate-50);
}
.flow-card__icon {
  width: 36px;
  height: 36px;
  border-radius: var(--radius);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.flow-card__icon--api { background: var(--orange-bg); color: var(--orange); }
.flow-card__icon--calc { background: var(--purple-bg); color: var(--purple); }
.flow-card__icon--user { background: var(--blue-bg); color: var(--blue); }
.flow-card__icon--config { background: var(--amber-bg); color: var(--amber); }
.flow-card__icon--geocode { background: var(--green-bg); color: var(--green); }
.flow-card__icon--db { background: var(--slate-100); color: var(--slate-500); }
.flow-card__icon--result { background: var(--slate-900); color: #fff; }
.flow-card__body { flex: 1; min-width: 0; }
.flow-card__title {
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--slate-800);
  margin-bottom: 2px;
  line-height: var(--lh-tight);
}
.flow-card__desc {
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  line-height: var(--lh);
}
.flow-card__link {
  font-size: var(--fs-xs);
  color: var(--blue);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: var(--sp-1);
  text-decoration: none;
}
.flow-card__link:hover { text-decoration: underline; }
.flow-card__freshness {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}
.flow-card__freshness--fresh { color: var(--green); }
.flow-card__freshness--aging { color: var(--amber-text); }
.flow-card__freshness--stale { color: var(--red); }

/* Flow Connector */
.flow-connector {
  display: flex;
  justify-content: center;
  padding: var(--sp-1) 0;
  color: var(--slate-300);
}
.flow-connector svg { width: 16px; height: 16px; }

/* ── Formula Explanation ── */
.formula-explain {
  padding: var(--sp-4);
  background: var(--slate-50);
  border: 1px solid var(--slate-200);
  border-radius: var(--radius);
  margin-top: var(--sp-3);
}
.formula-explain__title {
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  color: var(--slate-600);
  margin-bottom: var(--sp-2);
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.formula-explain__text {
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  line-height: var(--lh-loose);
}
.formula-explain__steps {
  list-style: none;
  margin-top: var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.formula-explain__step {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-3);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  line-height: var(--lh);
}
.formula-explain__step-num {
  width: 20px;
  height: 20px;
  border-radius: var(--radius-full);
  background: var(--slate-200);
  color: var(--slate-600);
  font-size: 11px;
  font-weight: var(--fw-bold);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 1px;
}
.formula-explain__step-label {
  font-weight: var(--fw-medium);
  color: var(--slate-700);
}
.formula-explain__step-value {
  font-family: var(--font-mono);
  font-weight: var(--fw-semibold);
  color: var(--slate-800);
  margin-left: auto;
  flex-shrink: 0;
}
.formula-explain__result {
  margin-top: var(--sp-3);
  padding-top: var(--sp-3);
  border-top: 2px solid var(--slate-200);
  font-size: var(--fs-sm);
  font-weight: var(--fw-bold);
  color: var(--slate-900);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.formula-explain__result-value {
  font-family: var(--font-mono);
  font-size: var(--fs-base);
}

/* ── Detail View ── */
.detail-view {
  padding: var(--sp-5);
}
.detail-tree {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
}
.detail-node {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--radius-sm);
  font-size: var(--fs-xs);
  transition: background var(--transition);
}
.detail-node:hover { background: var(--slate-50); }
.detail-node--indent-1 { padding-left: calc(var(--sp-3) + 20px); }
.detail-node--indent-2 { padding-left: calc(var(--sp-3) + 40px); }
.detail-node--indent-3 { padding-left: calc(var(--sp-3) + 60px); }
.detail-node--indent-4 { padding-left: calc(var(--sp-3) + 80px); }
.detail-node__dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
}
.detail-node__label {
  font-weight: var(--fw-semibold);
  color: var(--slate-700);
  font-size: var(--fs-xs);
}
.detail-node__desc {
  color: var(--text-muted);
  font-size: 10px;
}
.detail-node__ref {
  font-size: 10px;
  color: var(--blue);
  background: var(--blue-bg);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  margin-left: var(--sp-2);
  white-space: nowrap;
}
.detail-node__freshness {
  margin-left: auto;
  font-size: 10px;
  color: var(--text-muted);
  white-space: nowrap;
}
.detail-node__link {
  color: var(--text-muted);
  display: flex;
  align-items: center;
}
.detail-node__link:hover { color: var(--blue); }

/* ── Shared Reference Library ── */
.shared-refs {
  padding: var(--sp-4) var(--sp-5);
  background: var(--slate-50);
  border-top: 1px solid var(--border);
  margin-top: var(--sp-4);
}
.shared-refs__title {
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: var(--sp-3);
}
.shared-refs__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: var(--sp-3);
}
.shared-ref {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-4);
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: var(--fs-xs);
  cursor: pointer;
  transition: box-shadow var(--transition);
}
.shared-ref:hover { box-shadow: var(--shadow-sm); }
.shared-ref:focus-visible {
  outline: 2px solid var(--blue);
  outline-offset: 2px;
}
.shared-ref__icon {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
}
.shared-ref__label {
  font-weight: var(--fw-semibold);
  color: var(--slate-700);
}
.shared-ref__freshness {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-muted);
}

/* ── Legend ── */
.legend {
  display: flex;
  gap: var(--sp-4);
  padding: var(--sp-3) var(--sp-5);
  background: var(--slate-50);
  border-top: 1px solid var(--border);
  flex-wrap: wrap;
}
.legend__item {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  font-size: 11px;
  color: var(--text-muted);
}
.legend__dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
}

/* ── Accessibility ── */
:focus-visible {
  outline: 2px solid var(--blue);
  outline-offset: 2px;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0,0,0,0);
  white-space: nowrap;
  border-width: 0;
}
</style>
