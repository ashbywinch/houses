<script setup lang="ts">
import { ref } from 'vue'
import type { Provenance } from '../types'
import ProvenanceView from './ProvenanceView.vue'

withDefaults(defineProps<{
  provenance: Provenance
  title?: string
  hint?: string
  /** Render the opened tree as a floating panel below the trigger
   *  instead of in-flow. Screens whose summary row must not reflow
   *  (the property detail's monthly figures) pass this; the tree then
   *  overlays the page and never moves the figure. */
  popover?: boolean
}>(), {
  title: 'Result',
  popover: false,
})

// The ONE standard affordance for revealing a derivation (P8): every
// number's "how is this calculated?" opens through this component, never a
// per-screen variant.
const open = ref(false)
</script>

<template>
  <div class="provenance-toggle" :class="{ 'provenance-toggle--popover': popover }">
    <button
      class="provenance-toggle__trigger"
      type="button"
      :aria-expanded="open"
      :aria-label="open ? 'Hide calculation' : 'How is this calculated?'"
      :title="open ? 'Hide calculation' : 'How is this calculated?'"
      @click="open = !open"
    >
      <span class="provenance-toggle__icon" aria-hidden="true">ⓘ</span>
    </button>
    <p v-if="hint" class="provenance-toggle__hint">{{ hint }}</p>
    <div v-if="open" class="provenance-toggle__body">
      <ProvenanceView :provenance="provenance" :title="title" />
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
