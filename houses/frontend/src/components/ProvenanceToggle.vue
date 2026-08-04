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
      @click="open = !open"
    >
      {{ open ? 'Hide calculation' : 'How is this calculated?' }}
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
  font-size: 0.85rem;
  padding: 0;
  text-decoration: underline;
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
