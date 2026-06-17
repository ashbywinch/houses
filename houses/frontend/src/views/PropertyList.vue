<script setup lang="ts">
import { onMounted } from 'vue'
import { usePropertiesStore } from '../stores/properties'
import Header from '../components/Header.vue'
import PropertyCard from '../components/PropertyCard.vue'

const store = usePropertiesStore()

onMounted(() => {
  store.loadAll()
})
</script>

<template>
  <Header title="Properties" />
  <div class="page">
    <div v-if="store.loading" class="empty-state">
      <p class="empty-state__text">Loading...</p>
    </div>
    <div v-else-if="store.error" class="empty-state">
      <p class="empty-state__text">Error: {{ store.error }}</p>
    </div>
    <div v-else-if="store.rids.length === 0" class="empty-state">
      <p class="empty-state__text">No properties yet. Add one via the browser extension.</p>
    </div>
    <div v-else class="card-list">
      <template v-for="rid in store.rids" :key="rid">
        <PropertyCard :rid :data="store.properties[rid]" />
      </template>
    </div>
  </div>
</template>

<style scoped>
.page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 12px 12px 40px;
}
.card-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.empty-state {
  text-align: center;
  padding: 60px 20px;
}
.empty-state__text {
  font-size: 16px;
  color: var(--text-muted);
}
@media (min-width: 600px) {
  .card-list {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }
  .page { padding-left: 16px; padding-right: 16px; }
}
@media (min-width: 960px) {
  .card-list { grid-template-columns: 1fr 1fr 1fr; }
}
</style>
