<script setup lang="ts">
import { computed } from 'vue'
import type { Provenance } from '../types'

const props = withDefaults(defineProps<{
  provenance: Provenance
  showControls?: boolean
  depth?: number
}>(), {
  showControls: false,
  depth: 0,
})

const hasChildren = computed(() => {
  return props.provenance.sources && Object.keys(props.provenance.sources).length > 0
})

const dotColor = computed(() => {
  switch (props.provenance.sourceType) {
    case 'api': return 'var(--orange)'
    case 'calc': return 'var(--purple)'
    case 'user': return 'var(--blue)'
    case 'config': return 'var(--amber)'
    case 'geocode': return 'var(--green)'
    case 'db': return 'var(--slate-500)'
    default: return 'var(--slate-300)'
  }
})

const freshnessColor = computed(() => {
  if (!props.provenance.freshness || props.provenance.freshness === '—') return 'var(--slate-300)'
  const d = Date.now() - new Date(props.provenance.freshness).getTime()
  const days = d / (1000 * 60 * 60 * 24)
  if (days <= 7) return 'var(--green)'
  if (days <= 30) return 'var(--amber)'
  return 'var(--red)'
})

const freshnessIsDate = computed(() => {
  return !!props.provenance.freshness && props.provenance.freshness !== '—'
})

// Breadcrumb — build labels up the tree from sources
const breadcrumb = computed(() => {
  if (!props.showControls) return []
  const crumbs: string[] = [props.provenance.label]
  // Walk up is not available in recursive prop, so just show current node
  return crumbs
})

function sourceLabel(st: string | undefined): string {
  switch (st) {
    case 'api': return 'API'
    case 'calc': return 'Calc'
    case 'user': return 'User'
    case 'config': return 'Config'
    case 'geocode': return 'Geocode'
    case 'db': return 'DB'
    default: return 'Source'
  }
}
</script>

<template>
  <div class="prov-tree" :class="{ 'prov-tree--root': depth === 0 }">
    <!-- Breadcrumb trail (top level only) -->
    <div v-if="showControls && depth === 0" class="prov-breadcrumb">
      <span v-for="(crumb, i) in breadcrumb" :key="i" class="prov-breadcrumb__item">
        <span v-if="i > 0" class="prov-breadcrumb__sep">/</span>
        {{ crumb }}
      </span>
    </div>

    <!-- Expand/collapse all controls (top level only) -->
    <div v-if="showControls && depth === 0 && hasChildren" class="prov-controls">
      <button class="prov-controls__btn" @click="($el as HTMLElement)?.querySelectorAll<HTMLDetailsElement>('details')?.forEach(d => d.open = true)">
        Expand all
      </button>
      <button class="prov-controls__btn" @click="($el as HTMLElement)?.querySelectorAll<HTMLDetailsElement>('details')?.forEach(d => d.open = false)">
        Collapse all
      </button>
    </div>

    <div v-if="hasChildren" class="prov-node">
      <details class="prov-details" :open="depth < 1">
        <summary class="prov-node__head">
          <span class="prov-node__dot" :style="{ backgroundColor: dotColor }"></span>
          <span class="prov-node__source-badge" :class="`prov-node__source-badge--${provenance.sourceType || 'unknown'}`">
            {{ sourceLabel(provenance.sourceType) }}
          </span>
          <span class="prov-node__label">{{ provenance.label }}</span>
          <span v-if="provenance.description" class="prov-node__desc">— {{ provenance.description }}</span>
          <span v-if="freshnessIsDate" class="prov-node__freshness-dot" :style="{ backgroundColor: freshnessColor }" :title="'Fetched: ' + provenance.freshness"></span>
          <a v-if="provenance.url" :href="provenance.url" target="_blank" class="prov-node__link" rel="noopener" @click.stop>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
          </a>
        </summary>
        <div class="prov-node__children">
          <ProvenanceTree
            v-for="(src, key) in provenance.sources"
            :key="key"
            :provenance="src"
            :depth="depth + 1"
          />
        </div>
        <!-- Formula box -->
        <div v-if="provenance.formula" class="prov-formula">
          <div v-for="(line, li) in provenance.formula.lines" :key="li" class="prov-formula__line">
            <span class="prov-formula__label">{{ line.label }}</span>
            <span class="prov-formula__value">{{ line.value }}</span>
          </div>
          <div class="prov-formula__result">
            <span class="prov-formula__label">=</span>
            <span class="prov-formula__value">{{ provenance.formula.result }}</span>
          </div>
        </div>
      </details>
    </div>

    <!-- Leaf node (no children) -->
    <div v-else class="prov-node">
      <div class="prov-node__head prov-node__head--leaf">
        <span class="prov-node__dot" :style="{ backgroundColor: dotColor }"></span>
        <span class="prov-node__source-badge" :class="`prov-node__source-badge--${provenance.sourceType || 'unknown'}`">
          {{ sourceLabel(provenance.sourceType) }}
        </span>
        <span class="prov-node__label">{{ provenance.label }}</span>
        <span v-if="provenance.description" class="prov-node__desc">— {{ provenance.description }}</span>
        <span v-if="freshnessIsDate" class="prov-node__freshness-dot" :style="{ backgroundColor: freshnessColor }" :title="'Fetched: ' + provenance.freshness"></span>
        <a v-if="provenance.url" :href="provenance.url" target="_blank" class="prov-node__link" rel="noopener" @click.stop>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
        </a>
      </div>
      <!-- Formula box for leaf nodes -->
      <div v-if="provenance.formula" class="prov-formula">
        <div v-for="(line, li) in provenance.formula.lines" :key="li" class="prov-formula__line">
          <span class="prov-formula__label">{{ line.label }}</span>
          <span class="prov-formula__value">{{ line.value }}</span>
        </div>
        <div class="prov-formula__result">
          <span class="prov-formula__label">=</span>
          <span class="prov-formula__value">{{ provenance.formula.result }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.prov-tree { font-size: var(--fs-xs); }
.prov-tree--root { margin: 0; }

/* Breadcrumb */
.prov-breadcrumb {
  display: flex;
  gap: var(--sp-1);
  padding: var(--sp-2) 0;
  font-size: 10px;
  color: var(--slate-500);
  border-bottom: 1px solid var(--slate-200);
  margin-bottom: var(--sp-2);
}
.prov-breadcrumb__item { white-space: nowrap; }
.prov-breadcrumb__sep { margin: 0 var(--sp-1); color: var(--slate-300); }

/* Controls */
.prov-controls {
  display: flex;
  gap: var(--sp-2);
  margin-bottom: var(--sp-2);
}
.prov-controls__btn {
  font-size: 10px;
  color: var(--slate-500);
  background: var(--slate-100);
  border: 1px solid var(--slate-200);
  border-radius: var(--radius-sm);
  padding: 2px 8px;
  cursor: pointer;
  font-family: var(--font);
  transition: all var(--transition);
}
.prov-controls__btn:hover { background: var(--slate-200); color: var(--slate-700); }

/* Node */
.prov-node { margin-bottom: var(--sp-1); }

/* Details / summary — native expand/collapse */
.prov-details > .prov-node__head {
  cursor: pointer;
  user-select: none;
}
.prov-details > .prov-node__head::-webkit-details-marker { display: none; }
.prov-details > .prov-node__head::marker { display: none; content: ''; }
.prov-details > .prov-node__head::before {
  content: '▶';
  font-size: 7px;
  color: var(--slate-400);
  width: 12px;
  text-align: center;
  flex-shrink: 0;
  transition: transform var(--transition);
}
.prov-details[open] > .prov-node__head::before {
  transform: rotate(90deg);
}

.prov-node__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-1) var(--sp-2);
  border-radius: var(--radius-sm);
  transition: background var(--transition);
}
.prov-node__head:hover { background: var(--slate-100); }
.prov-node__head--leaf { padding-left: calc(var(--sp-2) + 12px); }

/* Source-type dot */
.prov-node__dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
}

/* Source badge */
.prov-node__source-badge {
  font-size: 9px;
  font-weight: var(--fw-bold);
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  background: var(--slate-100);
  color: var(--slate-500);
}
.prov-node__source-badge--api { background: var(--orange-bg); color: var(--orange-text); }
.prov-node__source-badge--calc { background: var(--purple-bg); color: var(--purple-text); }
.prov-node__source-badge--user { background: var(--blue-bg); color: var(--blue-text); }
.prov-node__source-badge--config { background: var(--amber-bg); color: var(--amber-text); }
.prov-node__source-badge--geocode { background: var(--green-bg); color: var(--green-text); }
.prov-node__source-badge--db { background: var(--slate-100); color: var(--slate-600); }

/* Label */
.prov-node__label {
  font-weight: var(--fw-semibold);
  color: var(--slate-700);
}
.prov-node__desc {
  color: var(--slate-500);
  font-size: 10px;
}

/* Freshness dot */
.prov-node__freshness-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
  margin-left: auto;
}

/* External link */
.prov-node__link {
  color: var(--slate-400);
  text-decoration: none;
  flex-shrink: 0;
  display: flex;
  align-items: center;
}
.prov-node__link:hover { color: var(--blue); }

/* Children container */
.prov-node__children {
  margin-left: 16px;
  padding-left: 12px;
  border-left: 1px solid var(--slate-200);
}

/* Formula box */
.prov-formula {
  margin: var(--sp-1) 0 var(--sp-1) calc(12px + var(--sp-2));
  padding: var(--sp-2) var(--sp-3);
  background: var(--slate-50);
  border-radius: var(--radius-sm);
  border: 1px solid var(--slate-200);
  font-family: var(--font-mono);
  font-size: 10px;
}
.prov-formula__line {
  display: flex;
  justify-content: space-between;
  gap: var(--sp-3);
  padding: 1px 0;
}
.prov-formula__label { color: var(--slate-500); }
.prov-formula__value { color: var(--slate-700); font-weight: var(--fw-semibold); text-align: right; }
.prov-formula__result {
  display: flex;
  justify-content: space-between;
  gap: var(--sp-3);
  padding-top: var(--sp-1);
  margin-top: var(--sp-1);
  border-top: 1px solid var(--slate-200);
  font-weight: var(--fw-bold);
}
.prov-formula__result .prov-formula__value { color: var(--slate-900); }
</style>
