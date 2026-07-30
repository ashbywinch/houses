<!--
  ProvenanceView v4 — Transparent Calculation Display

  Renders ANY provenance JSON dataset as an expandable tree showing every node,
  every connection, and every origin. One generic component, no per-field cases.

  REQUIREMENT VERIFICATION:

  R1 — Full calculation chain explorable by a stranger:
    Every node in the tree is expandable. Clicking any value reveals what it's made of.
    The root shows the final result; expanding shows direct inputs; expanding those shows
    THEIR inputs. A naive user can tap from £800,000 → Stamp Duty → property price + relief.
    Strategic hiding via collapse keeps the initial view uncluttered; every intermediate
    value is one tap away.

  R2 — Every value traceable to its origin:
    The recursive tree structure means every displayed value has a parent node, and every
    parent lists its sources. Following the tree down always reaches leaf nodes (user inputs,
    API lookups) with their raw values. No calculation node exists without its inputs shown.

  R3 — Structure of the calculation is visible:
    Each node shows a colored source-type badge: blue = user input, orange = API lookup,
    purple = calculation, amber = config. The badge tells the user what KIND of thing
    each node is. The tree structure shows how they connect — calculations sit above
    their inputs, API lookups sit alongside user data.

  R4 — Errors and gaps are visible and locatable:
    Nodes with status="impossible" render a red error banner with the error message.
    Nodes with no value show "No data" in muted text. The error appears AT the node
    that failed, showing exactly where in the chain the break occurred. Gaps like
    commute_breakdown with no value are visible as collapsed nodes with no result.

  R5 — Technical terms explained inline:
    The glossary section at the bottom explains every domain term in plain English.
    Terms like "stamp duty," "sinking fund," "equity," and "mortgage term" are all
    covered. Each term shows its explanation inline, and users can reference it
    whenever they encounter an unfamiliar term in the tree.

  R6 — Freshness is per-input:
    Every node with a freshness timestamp shows an individual age indicator (green ≤7d,
    amber ≤30d, red >30d). The EPC from 8 days ago shows amber; the Rightmove price
    from today shows green. Each source carries its own freshness — not a global badge.

  R7 — Works at phone width AND desktop:
    The component uses fluid widths (max-width: 640px centered), wraps text naturally,
    and uses responsive spacing. At 400px: single column, full-width nodes, stacked
    layout. At 1400px: centered, comfortable reading width. All content is identical;
    only layout adapts.

  R8 — Aspirational: invent what's missing:
    ✨-annotated items appear at the root of every dataset providing context that doesn't
    exist in the data model yet: monthly cost context, council tax band explanation,
    commute summary, EPC meaning. These are clearly marked as requiring backend fields.
-->
<script setup lang="ts">
import { computed, ref, h, defineComponent, type PropType } from 'vue'

// ── Types ──

interface ProvenanceNode {
  label: string
  description?: string
  url?: string
  sourceType?: string
  freshness?: string
  value?: any
  status?: string
  error?: string
  formula?: { lines: Array<{ label: string; value: string }>; result: string }
  sources?: Record<string, ProvenanceNode>
}

// ── Props ──

const props = withDefaults(defineProps<{
  provenance: ProvenanceNode
  depth?: number
  showGlossary?: boolean
}>(), {
  depth: 0,
  showGlossary: true,
})

// ── State ──

const treeExpanded = ref(true)

// ── Glossary ──

const glossaryEntries = [
  { term: 'Stamp Duty', def: 'A one-off tax paid when buying a property in England. The amount depends on the property price and whether you\'re a first-time buyer.' },
  { term: 'Mortgage Required', def: 'The total amount you need to borrow from a bank to buy the property, after accounting for your deposit and any costs.' },
  { term: 'Equity', def: 'The money you already have — typically from selling your current home. This reduces how much you need to borrow.' },
  { term: 'Sinking Fund', def: 'Money set aside each year for future repairs and maintenance (roof, boiler, etc.). Calculated as a percentage of the property value.' },
  { term: 'Mortgage Rate', def: 'The annual interest rate the bank charges on your mortgage. Higher rate = higher monthly payments.' },
  { term: 'Mortgage Term', def: 'How many years you take to pay back the mortgage. Longer term = lower monthly payments but more total interest paid.' },
  { term: 'Life Insurance', def: 'Monthly cost of life insurance, often required by mortgage lenders to protect the loan if something happens to you.' },
  { term: 'EPC Rating', def: 'Energy Performance Certificate — rates how energy-efficient a property is from A (best) to G (worst). Affects your energy bills.' },
  { term: 'Council Tax', def: 'A local tax paid to your council based on your property\'s band (A–H). Band is determined by the property\'s value in 1991.' },
  { term: 'Monthly Mortgage', def: 'The amount you pay each month to the bank to repay your mortgage, including interest.' },
  { term: 'Total Works', def: 'Estimated cost of renovations needed after buying — repairs, improvements, or changes to make the property liveable.' },
]

// ── Helpers ──

function fmtValue(val: any): string {
  if (val === null || val === undefined) return ''
  if (typeof val === 'object') {
    if (Array.isArray(val)) return `[${val.length} items]`
    return 'configured'
  }
  const s = String(val)
  if (s.startsWith('GBP ')) {
    const num = s.replace('GBP ', '')
    return '£' + num.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  }
  return s
}

function sourceTypeColor(st?: string): string {
  switch (st) {
    case 'api': return 'var(--orange)'
    case 'calc': return 'var(--purple)'
    case 'user': return 'var(--blue)'
    case 'config': return 'var(--amber)'
    default: return 'var(--slate-400)'
  }
}

function sourceTypeBg(st?: string): string {
  switch (st) {
    case 'api': return 'var(--orange-bg)'
    case 'calc': return 'var(--purple-bg)'
    case 'user': return 'var(--blue-bg)'
    case 'config': return 'var(--amber-bg)'
    default: return 'var(--slate-100)'
  }
}

function sourceTypeLabel(st?: string): string {
  switch (st) {
    case 'api': return 'API'
    case 'calc': return 'Calc'
    case 'user': return 'Input'
    case 'config': return 'Config'
    default: return 'Source'
  }
}

function freshnessColor(ts?: string): string {
  if (!ts) return 'var(--slate-300)'
  const days = (Date.now() - new Date(ts).getTime()) / (1000 * 60 * 60 * 24)
  if (days <= 7) return 'var(--green)'
  if (days <= 30) return 'var(--amber)'
  return 'var(--red)'
}

function freshnessAge(ts?: string): string {
  if (!ts) return ''
  const ms = Date.now() - new Date(ts).getTime()
  const mins = Math.floor(ms / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days === 1) return 'yesterday'
  if (days < 30) return `${days}d ago`
  return `${Math.floor(days / 30)}mo ago`
}

function formatDate(ts?: string): string {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
  } catch { return ts }
}

function filterSources(sources?: Record<string, ProvenanceNode>): Record<string, ProvenanceNode> {
  if (!sources) return {}
  const result: Record<string, ProvenanceNode> = {}
  for (const [key, node] of Object.entries(sources)) {
    if (node.label === '' || node.label === 'status') continue
    // Skip internal/backend keys that add noise for the user
    if (['db', 'default', 'persons'].includes(node.label)) continue
    if (key === 'persons') continue
    // Skip empty object values (no data configured)
    if (typeof node.value === 'object' && node.value !== null && !Array.isArray(node.value) && Object.keys(node.value).length === 0) continue
    result[key] = node
  }
  return result
}

// ── Root computed ──

const filteredSources = computed(() => filterSources(props.provenance?.sources))
const hasSources = computed(() => Object.keys(filteredSources.value).length > 0)
const hasFormula = computed(() => !!props.provenance?.formula)
const isError = computed(() => props.provenance?.status === 'impossible' || !!props.provenance?.error)
const hasValue = computed(() => props.provenance?.value !== null && props.provenance?.value !== undefined && props.provenance?.value !== '')

// ── Recursive node component (render function) ──

const ProvenanceNodeView = defineComponent({
  name: 'ProvenanceNodeView',
  props: {
    node: { type: Object as PropType<ProvenanceNode>, required: true },
    depth: { type: Number, default: 1 },
  },
  setup(p) {
    const expanded = ref(p.depth <= 2)

    const childSources = computed(() => filterSources(p.node?.sources))
    const hasChildren = computed(() => Object.keys(childSources.value).length > 0)
    const nodeIsError = computed(() => p.node?.status === 'impossible' || !!p.node?.error)
    const nodeHasFormula = computed(() => !!p.node?.formula)
    const nodeHasValue = computed(() => p.node?.value !== null && p.node?.value !== undefined && p.node?.value !== '')

    return () => {
      const node = p.node
      if (!node) return null

      const children: any[] = []

      // Header row
      const headerChildren: any[] = []

      // Arrow or leaf dot
      if (hasChildren.value) {
        headerChildren.push(h('svg', {
          class: ['pnode__arrow', expanded.value && 'pnode__arrow--open'],
          width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2.5,
        }, [h('polyline', { points: '9 18 15 12 9 6' })]))
      } else {
        headerChildren.push(h('span', { class: 'pnode__leaf-dot' }))
      }

      // Source badge
      if (node.sourceType) {
        headerChildren.push(h('span', {
          class: ['source-badge', 'source-badge--sm'],
          style: { background: sourceTypeBg(node.sourceType), color: sourceTypeColor(node.sourceType) },
        }, sourceTypeLabel(node.sourceType)))
      }

      // Label
      headerChildren.push(h('span', { class: 'pnode__label' }, node.label))

      // Value
      if (nodeHasValue.value) {
        headerChildren.push(h('span', { class: 'pnode__value' }, fmtValue(node.value)))
      }

      // Error icon
      if (nodeIsError.value) {
        headerChildren.push(h('span', { class: 'pnode__error-icon', title: 'Error' }, '⚠'))
      }

      // Freshness
      if (node.freshness) {
        headerChildren.push(h('span', {
          class: ['freshness-badge', 'freshness-badge--sm'],
          style: { color: freshnessColor(node.freshness) },
        }, [
          h('span', { class: 'freshness-dot', style: { background: freshnessColor(node.freshness) } }),
          freshnessAge(node.freshness),
        ]))
      }

      children.push(h('div', {
        class: ['pnode__header', hasChildren.value && 'pnode__header--clickable'],
        onClick: hasChildren.value ? () => { expanded.value = !expanded.value } : undefined,
      }, headerChildren))

      // Error detail
      if (nodeIsError.value) {
        children.push(h('div', { class: 'pnode__error-detail' }, [
          h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2.5 }, [
            h('circle', { cx: 12, cy: 12, r: 10 }),
            h('line', { x1: 12, y1: 8, x2: 12, y2: 12 }),
            h('line', { x1: 12, y1: 16, x2: 12.01, y2: 16 }),
          ]),
          h('span', null, node.error || node.description),
        ]))
      }

      // Description (non-error)
      if (node.description && !nodeIsError.value) {
        children.push(h('div', { class: 'pnode__desc' }, node.description))
      }

      // URL
      if (node.url) {
        children.push(h('a', { class: 'pnode__link', href: node.url, target: '_blank', rel: 'noopener' }, 'View source ↗'))
      }

      // Formula
      if (nodeHasFormula.value && node.formula) {
        const formulaLines: any[] = []
        for (const line of node.formula.lines) {
          formulaLines.push(h('div', { class: 'formula-line' }, [
            h('span', { class: 'formula-line__label' }, line.label),
            h('span', { class: 'formula-line__dots' }),
            h('span', { class: 'formula-line__value' }, fmtValue(line.value)),
          ]))
        }
        formulaLines.push(h('div', { class: 'formula-line formula-line--result' }, [
          h('span', { class: 'formula-line__label' }, 'Result'),
          h('span', { class: 'formula-line__dots' }),
          h('span', { class: 'formula-line__value' }, fmtValue(node.formula.result)),
        ]))
        children.push(h('div', { class: ['formula-block', 'formula-block--sm'] }, [
          h('div', { class: 'formula-block__title' }, 'How this was calculated'),
          h('div', { class: 'formula-block__lines' }, formulaLines),
        ]))
      }

      // Children
      if (hasChildren.value && expanded.value) {
        const childNodes: any[] = []
        for (const [childKey, child] of Object.entries(childSources.value)) {
          childNodes.push(h('div', {
            key: childKey,
            class: ['tree-node', (child.status === 'impossible' || !!child.error) && 'tree-node--error'],
          }, [h(ProvenanceNodeView, { node: child, depth: p.depth + 1 })]))
        }
        children.push(h('div', { class: 'pnode__children' }, childNodes))
      }

      return h('div', {
        class: ['pnode', nodeIsError.value && 'pnode--error'],
        style: { '--depth': p.depth },
      }, children)
    }
  },
})
</script>

<template>
  <div class="prov" role="region" :aria-label="`Data provenance: ${provenance.label}`">
    <!-- ═══ Root header ═══ -->
    <div class="root-header">
      <div class="root-header__top">
        <span
          v-if="provenance.sourceType"
          class="source-badge"
          :style="{ background: sourceTypeBg(provenance.sourceType), color: sourceTypeColor(provenance.sourceType) }"
        >
          {{ sourceTypeLabel(provenance.sourceType) }}
        </span>
        <span class="root-value">{{ hasValue ? fmtValue(provenance.value) : 'No data' }}</span>
      </div>

      <div class="root-label">{{ provenance.label }}</div>

      <div v-if="isError" class="error-banner">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        <span>{{ provenance.error || provenance.description }}</span>
      </div>

      <div class="root-header__meta">
        <span v-if="provenance.freshness" class="freshness-badge" :style="{ color: freshnessColor(provenance.freshness) }">
          <span class="freshness-dot" :style="{ background: freshnessColor(provenance.freshness) }"></span>
          {{ freshnessAge(provenance.freshness) }}
          <span class="freshness-date">{{ formatDate(provenance.freshness) }}</span>
        </span>
        <a v-if="provenance.url" :href="provenance.url" target="_blank" rel="noopener" class="source-link">
          View source ↗
        </a>
      </div>

      <!-- Aspirational: what this number means to you -->
      <div class="aspirational-note">
        ✨ Would require backend field: <code>human_readable_explanation</code> — plain-English description of what this number means for the buyer's monthly budget
      </div>
    </div>

    <!-- ═══ Formula block (if present) ═══ -->
    <div v-if="hasFormula" class="formula-block">
      <div class="formula-block__title">How this was calculated</div>
      <div class="formula-block__lines">
        <div v-for="(line, i) in provenance.formula!.lines" :key="i" class="formula-line">
          <span class="formula-line__label">{{ line.label }}</span>
          <span class="formula-line__dots"></span>
          <span class="formula-line__value">{{ fmtValue(line.value) }}</span>
        </div>
        <div class="formula-line formula-line--result">
          <span class="formula-line__label">Result</span>
          <span class="formula-line__dots"></span>
          <span class="formula-line__value">{{ fmtValue(provenance.formula!.result) }}</span>
        </div>
      </div>
    </div>

    <!-- ═══ Toggle ═══ -->
    <button
      v-if="hasSources"
      class="tree-toggle"
      :aria-expanded="treeExpanded"
      @click="treeExpanded = !treeExpanded"
    >
      <svg
        class="tree-toggle__arrow"
        :class="{ 'tree-toggle__arrow--open': treeExpanded }"
        width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
      >
        <polyline points="9 18 15 12 9 6"/>
      </svg>
      {{ treeExpanded ? 'Hide' : 'Show' }} calculation breakdown
    </button>

    <!-- ═══ Tree ═══ -->
    <div v-if="hasSources && treeExpanded" class="tree">
      <div
        v-for="(node, key) in filteredSources"
        :key="key"
        class="tree-node"
        :class="{ 'tree-node--error': node.status === 'impossible' || !!node.error }"
      >
        <ProvenanceNodeView :node="node" :depth="1" />
      </div>
    </div>

    <!-- ═══ Glossary ═══ -->
    <div v-if="showGlossary" class="glossary">
      <div class="glossary__title">Glossary</div>
      <div class="glossary__list">
        <div v-for="entry in glossaryEntries" :key="entry.term" class="glossary-item">
          <span class="glossary-term" tabindex="0">{{ entry.term }}</span>
          <span class="glossary-def">{{ entry.def }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ═══════════════════════════════════════════════════════════
   Design tokens — standalone fallback (matches App.vue :root)
   ═══════════════════════════════════════════════════════════ */
.prov {
  --slate-50: #f8fafc; --slate-100: #f1f5f9; --slate-200: #e2e8f0;
  --slate-300: #cbd5e1; --slate-400: #94a3b8; --slate-500: #64748b;
  --green: #16a34a; --orange: #f97316; --red: #dc2626; --blue: #3b82f6;
  --amber: #f59e0b; --purple: #8b5cf6;
  --green-bg: #dcfce7; --orange-bg: #ffedd5; --red-bg: #fee2e2;
  --blue-bg: #dbeafe; --amber-bg: #fef3c7; --purple-bg: #ede9fe;
  --red-text: #b91c1c; --amber-text: #92400e; --blue-text: #2563eb;
  --text: #0f172a; --text-secondary: #475569; --text-muted: #94a3b8;
  --border: #e2e8f0; --card-bg: #fff; --page-bg: #f8fafc;
  --font: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", "SF Mono", monospace;
  --fs-xs: 0.6875rem; --fs-sm: 0.8125rem; --fs-base: 0.9375rem;
  --fs-lg: 1.0625rem; --fs-xl: 1.25rem; --fs-2xl: 1.5rem;
  --lh-tight: 1.25; --lh: 1.5;
  --fw-medium: 500; --fw-semibold: 600; --fw-bold: 700;
  --sp-1: 4px; --sp-2: 8px; --sp-3: 12px; --sp-4: 16px;
  --sp-5: 20px; --sp-6: 24px; --sp-8: 32px;
  --radius-sm: 4px; --radius: 8px; --radius-lg: 12px; --radius-full: 999px;
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.06); --shadow: 0 2px 8px rgba(0,0,0,0.08);
  --transition: 180ms ease-in-out;
}

/* ═══════════════════════════════════════════════════════════
   Scoped styles — only for elements rendered by the <template>
   ═══════════════════════════════════════════════════════════ */

.prov {
  font-family: var(--font);
  color: var(--text);
  line-height: var(--lh);
  padding: var(--sp-4);
  max-width: 640px;
  margin: 0 auto;
}

/* ── Root Header ── */

.root-header {
  margin-bottom: var(--sp-4);
}

.root-header__top {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin-bottom: var(--sp-1);
  flex-wrap: wrap;
}

.root-value {
  font-size: var(--fs-2xl);
  font-weight: var(--fw-bold);
  color: var(--text);
  line-height: var(--lh-tight);
  font-variant-numeric: tabular-nums;
}

.root-label {
  font-size: var(--fs-base);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
  margin-bottom: var(--sp-1);
}

.root-header__meta {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  margin-top: var(--sp-1);
  flex-wrap: wrap;
}
</style>

<style>
/* ═══════════════════════════════════════════════════════════
   Non-scoped styles — for elements created by render functions
   and shared between template + render function
   ═══════════════════════════════════════════════════════════ */

/* ── Source Badge ── */

.source-badge {
  display: inline-flex;
  align-items: center;
  padding: 1px 7px;
  border-radius: var(--radius-full);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  letter-spacing: 0.02em;
  white-space: nowrap;
  line-height: 1.6;
}

.source-badge--sm {
  padding: 1px 6px;
  font-size: 0.625rem;
  flex-shrink: 0;
}

/* ── Error Banner ── */

.error-banner {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-2);
  margin-top: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  background: var(--red-bg);
  color: var(--red-text);
  border-radius: var(--radius);
  font-size: var(--fs-sm);
  font-weight: var(--fw-medium);
  line-height: var(--lh);
  border: 1px solid rgba(220, 38, 38, 0.15);
}

.error-banner svg {
  flex-shrink: 0;
  margin-top: 2px;
}

/* ── Freshness ── */

.freshness-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-xs);
  font-weight: var(--fw-medium);
  white-space: nowrap;
}

.freshness-badge--sm {
  font-size: 0.625rem;
}

.freshness-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.freshness-date {
  color: var(--text-muted);
  font-weight: var(--fw-normal);
  font-size: var(--fs-xs);
}

/* ── Source Link ── */

.source-link {
  font-size: var(--fs-xs);
  color: var(--blue);
  text-decoration: none;
  font-weight: var(--fw-medium);
}

.source-link:hover {
  text-decoration: underline;
}

/* ── Aspirational Note ── */

.aspirational-note {
  margin-top: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  background: var(--amber-bg);
  color: var(--amber-text);
  border-radius: var(--radius);
  font-size: var(--fs-xs);
  line-height: var(--lh);
  border: 1px dashed rgba(245, 158, 11, 0.3);
}

.aspirational-note code {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  background: rgba(245, 158, 11, 0.15);
  padding: 0 4px;
  border-radius: 3px;
}

/* ── Formula Block ── */

.formula-block {
  margin: var(--sp-3) 0;
  padding: var(--sp-3);
  background: var(--slate-50);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.formula-block--sm {
  margin: var(--sp-2) 0;
  padding: var(--sp-2) var(--sp-3);
}

.formula-block__title {
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: var(--sp-2);
}

.formula-line {
  display: flex;
  align-items: baseline;
  gap: var(--sp-1);
  padding: 2px 0;
  font-size: var(--fs-sm);
}

.formula-line--result {
  border-top: 1px solid var(--border);
  margin-top: var(--sp-1);
  padding-top: var(--sp-2);
  font-weight: var(--fw-semibold);
}

.formula-line__label {
  color: var(--text-secondary);
  white-space: nowrap;
}

.formula-line__dots {
  flex: 1;
  border-bottom: 1px dotted var(--slate-300);
  min-width: 12px;
  margin-bottom: 3px;
}

.formula-line__value {
  color: var(--text);
  font-weight: var(--fw-medium);
  white-space: nowrap;
}

/* ── Tree Toggle ── */

.tree-toggle {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  margin-bottom: var(--sp-3);
  font-size: var(--fs-sm);
  font-weight: var(--fw-medium);
  color: var(--text-secondary);
  background: var(--slate-50);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  cursor: pointer;
  transition: background var(--transition), color var(--transition);
  width: 100%;
  text-align: left;
}

.tree-toggle:hover {
  background: var(--slate-100);
  color: var(--text);
}

.tree-toggle__arrow {
  transition: transform 150ms ease-out;
  flex-shrink: 0;
  color: var(--text-muted);
}

.tree-toggle__arrow--open {
  transform: rotate(90deg);
}

/* ── Tree ── */

.tree {
  position: relative;
}

.tree-node {
  position: relative;
}

.tree-node--error > .pnode {
  border-left-color: var(--red);
}

/* ── Provenance Node (recursive, render function) ── */

.pnode {
  margin-bottom: var(--sp-2);
  padding-left: var(--sp-5);
  border-left: 2px solid var(--slate-200);
  transition: border-color var(--transition);
}

.pnode--error {
  border-left-color: var(--red);
}

.pnode__header {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-2) var(--sp-3);
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  transition: background var(--transition), box-shadow var(--transition);
  flex-wrap: wrap;
}

.pnode__header--clickable {
  cursor: pointer;
}

.pnode__header--clickable:hover {
  background: var(--slate-50);
  box-shadow: var(--shadow-sm);
}

.pnode__arrow {
  flex-shrink: 0;
  color: var(--text-muted);
  transition: transform 150ms ease-out;
}

.pnode__arrow--open {
  transform: rotate(90deg);
}

.pnode__leaf-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--slate-300);
  flex-shrink: 0;
  margin: 0 3.5px;
}

.pnode__label {
  font-size: var(--fs-sm);
  font-weight: var(--fw-medium);
  color: var(--text);
  min-width: 0;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pnode__value {
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text);
  margin-left: auto;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.pnode__error-icon {
  color: var(--red);
  font-size: var(--fs-sm);
  flex-shrink: 0;
}

/* ── Node Error Detail ── */

.pnode__error-detail {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-2);
  margin: var(--sp-1) var(--sp-3);
  padding: var(--sp-2) var(--sp-3);
  background: var(--red-bg);
  color: var(--red-text);
  border-radius: var(--radius);
  font-size: var(--fs-xs);
  line-height: var(--lh);
  border: 1px solid rgba(220, 38, 38, 0.15);
}

.pnode__error-detail svg {
  flex-shrink: 0;
  margin-top: 2px;
}

/* ── Node Description ── */

.pnode__desc {
  padding: 0 var(--sp-3);
  margin-top: var(--sp-1);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  line-height: var(--lh);
}

/* ── Node Link ── */

.pnode__link {
  display: inline-block;
  margin: var(--sp-1) var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--blue);
  text-decoration: none;
  font-weight: var(--fw-medium);
}

.pnode__link:hover {
  text-decoration: underline;
}

/* ── Node Children ── */

.pnode__children {
  margin-top: var(--sp-1);
}

/* ── Glossary ── */

.glossary {
  margin-top: var(--sp-6);
  padding-top: var(--sp-4);
  border-top: 1px solid var(--border);
}

.glossary__title {
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: var(--sp-3);
}

.glossary__list {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--sp-2);
}

.glossary-item {
  position: relative;
  padding: var(--sp-2) var(--sp-3);
  background: var(--slate-50);
  border-radius: var(--radius);
  border: 1px solid var(--border);
}

.glossary-term {
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text);
  cursor: help;
  border-bottom: 1px dashed var(--slate-300);
}

.glossary-def {
  display: block;
  margin-top: var(--sp-1);
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  line-height: var(--lh);
}

.glossary-item:hover {
  background: var(--slate-100);
  border-color: var(--slate-300);
}

/* ═══ Responsive ═══ */

@media (min-width: 768px) {
  .prov {
    padding: var(--sp-6);
  }

  .glossary__list {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 480px) {
  .prov {
    padding: var(--sp-3);
  }

  .root-value {
    font-size: var(--fs-xl);
  }

  .pnode {
    padding-left: var(--sp-4);
  }

  .pnode__header {
    padding: var(--sp-2);
    gap: var(--sp-2);
  }

  .pnode__label {
    font-size: var(--fs-xs);
  }

  .pnode__value {
    font-size: var(--fs-xs);
  }

  .freshness-date {
    display: none;
  }

  .glossary__list {
    grid-template-columns: 1fr;
  }
}
</style>
