<script setup lang="ts">
import type { Provenance } from '../types'

defineProps<{
  provenance: Provenance
}>()
</script>

<template>
  <div class="provenance-tree">
    <span class="provenance-label">{{ provenance.label }}</span>
    <a
      v-if="provenance.url"
      :href="provenance.url"
      class="provenance-link"
      target="_blank"
      rel="noopener"
    >&#x1F517;</a>
    <span v-if="provenance.description" class="provenance-desc">{{ provenance.description }}</span>
    <div v-if="provenance.sources" class="provenance-children">
      <div
        v-for="(src, key) in provenance.sources"
        :key="key"
        class="provenance-child"
      >
        <ProvenanceTree :provenance="src" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.provenance-tree {
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.4;
}
.provenance-label {
  font-style: italic;
}
.provenance-link {
  margin-left: 3px;
  text-decoration: none;
  font-size: 12px;
}
.provenance-link:hover {
  opacity: 0.7;
}
.provenance-desc {
  margin-left: 4px;
}
.provenance-children {
  margin-left: 12px;
  margin-top: 2px;
  border-left: 1px solid var(--border-color, #ddd);
  padding-left: 8px;
}
.provenance-child {
  margin-top: 2px;
}
</style>
