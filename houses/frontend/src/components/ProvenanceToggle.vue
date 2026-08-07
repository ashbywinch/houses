<script setup lang="ts">
import { ref } from 'vue'
import type { Provenance } from '../types'
import ProvenanceView from './ProvenanceView.vue'

withDefaults(defineProps<{
  provenance: Provenance
  title?: string
  hint?: string
}>(), {
  title: 'Result',
})

// The ONE standard affordance for revealing a derivation (P8): every
// number's "how is this calculated?" opens through this component, never a
// per-screen variant.
const open = ref(false)
</script>

<template>
  <div class="provenance-toggle">
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
</style>
