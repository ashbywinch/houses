<script setup lang="ts">
import { ref } from 'vue'
import type { Provenance } from '../types'
import ProvenanceView from './ProvenanceView.vue'
import { useProvenanceStore } from '../stores/provenance'

const props = withDefaults(defineProps<{
  /** Direct provenance mode — ONLY for surfaces whose payload is authored
   *  WITH its derivation embedded (the settings payload, by design).
   *  Property surfaces must NOT use this: their wires are provenance-free
   *  and the tree loads by rid+path below. */
  provenance?: Provenance
  /** The property whose derivation this reveals (property surfaces). */
  rid?: string
  /** Dotted path into the property's provenance map — the contract the
   *  server's on-demand /provenance endpoint serves (e.g.
   *  "affordability.monthly_mortgage"). */
  path?: string
  title?: string
  hint?: string
  /** Render the opened tree as a floating panel below the trigger
   *  instead of in-flow. Screens whose summary row must not reflow
   *  (the property detail's monthly figures) pass this; the tree then
   *  overlays the page and never moves the figure. */
  popover?: boolean
  /** Optional per-surface shaping of the served tree (e.g. the commute
   *  section drops fuel sources a mode can't use). Applied after load. */
  transform?: (p: Provenance) => Provenance
}>(), {
  title: 'Result',
  popover: false,
})

// The ONE standard affordance for revealing a derivation (P8): every
// number's "how is this calculated?" opens through this component, never a
// per-screen variant. The wire carries NO provenance (P8: revealed only
// on demand) — the open click is the only thing that ever loads it.
const open = ref(false)
const provenance = ref<Provenance>()
const failed = ref(false)
const store = useProvenanceStore()

async function reveal() {
  open.value = !open.value
  if (!open.value || provenance.value) return
  if (props.provenance) {
    provenance.value = props.provenance
    return
  }
  if (!props.rid || !props.path) return
  try {
    const map = await store.loadProvenance(props.rid)
    const served = map[props.path]
    provenance.value = served === undefined ? undefined : (props.transform?.(served) ?? served)
  } catch {
    failed.value = true
  }
}
</script>

<template>
  <div class="provenance-toggle" :class="{ 'provenance-toggle--popover': popover }">
    <button
      class="provenance-toggle__trigger"
      type="button"
      :aria-expanded="open"
      :aria-label="open ? 'Hide calculation' : 'How is this calculated?'"
      :title="open ? 'Hide calculation' : 'How is this calculated?'"
      @click="reveal"
    >
      <span class="provenance-toggle__icon" aria-hidden="true">ⓘ</span>
    </button>
    <p v-if="hint" class="provenance-toggle__hint">{{ hint }}</p>
    <div v-if="open" class="provenance-toggle__body">
      <ProvenanceView v-if="provenance" :provenance="provenance" :title="title" />
      <p v-else-if="failed" class="provenance-toggle__state">Couldn't load the derivation.</p>
      <p v-else class="provenance-toggle__state">Loading derivation…</p>
    </div>
  </div>
</template>

<style scoped>
.provenance-toggle__trigger {
  background: none;
  border: none;
  color: var(--blue);
  cursor: pointer;
  padding: 0;
  line-height: 1;
}
.provenance-toggle__icon {
  font-size: 1rem;
  display: inline-block;
}
.provenance-toggle__hint {
  color: var(--text-muted);
  font-size: 0.85rem;
  margin: 0.25rem 0 0;
}
.provenance-toggle__state {
  color: var(--text-muted);
  font-size: 0.85rem;
  margin: 0.25rem 0 0;
}
.provenance-toggle__body {
  margin-top: 0.5rem;
}
/* Floating form (popover): the tree overlays the page below the
   trigger, anchored to its right edge — it must never reflow the
   summary row holding the figure. */
.provenance-toggle--popover {
  position: relative;
}
.provenance-toggle--popover .provenance-toggle__body {
  position: absolute;
  top: calc(100% + 0.35rem);
  right: 0;
  left: auto;
  width: min(640px, calc(100vw - 2rem));
  max-height: min(60vh, 480px);
  overflow-y: auto;
  z-index: 40;
  background: var(--card-bg, #fff);
  border: 1px solid var(--border, rgba(0, 0, 0, 0.25));
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  padding: var(--sp-2);
}
</style>