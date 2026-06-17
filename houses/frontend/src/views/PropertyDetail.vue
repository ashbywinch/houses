<script setup lang="ts">
import { onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePropertiesStore } from '../stores/properties'
import Header from '../components/Header.vue'

const route = useRoute()
const router = useRouter()
const store = usePropertiesStore()

const rid = computed(() => route.params.rid as string)

const property = computed(() => store.properties[rid.value])

onMounted(() => {
  if (rid.value) {
    store.loadProperty(rid.value)
  }
})

const location = computed(() => {
  if (!property.value?.best_location.succeeded) return null
  return property.value.best_location.value
})

const address = computed(() => {
  if (!property.value?.best_address.succeeded) return null
  return property.value.best_address.value
})
</script>

<template>
  <Header title="Property Detail">
    <template #actions>
      <button class="btn--icon" @click="router.push('/')">←</button>
    </template>
  </Header>
  <div class="page">
    <div v-if="store.loading" class="empty-state">
      <p class="empty-state__text">Loading property...</p>
    </div>
    <div v-else-if="!property" class="empty-state">
      <p class="empty-state__text">Property not found.</p>
      <button class="btn--primary" @click="router.push('/')">Back to list</button>
    </div>
    <template v-else>
      <div class="detail__summary-bar">
        <span class="detail__price">{{ rid }}</span>
      </div>

      <section class="detail__section">
        <div class="detail__section-header">📍 Location</div>
        <div class="detail__field">
          <div class="detail__field-label">Address</div>
          <div class="detail__field-value">{{ address ?? 'Unknown' }}</div>
          <div class="detail__field-provenance">
            <span class="provenance-badge">{{ property.best_address.provenance.label || 'unknown' }}</span>
          </div>
        </div>
        <div v-if="location" class="detail__field">
          <div class="detail__field-label">Coordinates</div>
          <div class="detail__field-value">{{ location.lat.toFixed(4) }}, {{ location.lon.toFixed(4) }}</div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 12px 16px 40px;
}
.btn--icon {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: rgba(255,255,255,0.12);
  color: #fff;
  font-size: 20px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0.5;
  border: none;
  cursor: pointer;
}
.empty-state {
  text-align: center;
  padding: 60px 20px;
}
.empty-state__text {
  font-size: 16px;
  color: var(--text-muted);
}
.btn--primary {
  display: inline-block;
  padding: 0.6em 1.2em;
  font-size: 0.95em;
  border-radius: 6px;
  border: none;
  background: #1565c0;
  color: #fff;
  cursor: pointer;
  text-decoration: none;
  margin-top: 1em;
}
.detail__summary-bar {
  display: flex;
  gap: 1em;
  align-items: center;
  padding: 1em 0;
  font-size: 1.1em;
  border-bottom: 1px solid var(--border);
  margin-bottom: 1em;
}
.detail__price { font-weight: 700; }
.detail__section {
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 1em;
  overflow: hidden;
}
.detail__section-header {
  padding: 0.75em 1em;
  background: #f9f9f9;
  font-weight: 600;
  font-size: 0.95em;
  border-bottom: 1px solid var(--border);
}
.detail__field {
  padding: 0.75em 1em;
  border-bottom: 1px solid #f0f0f0;
}
.detail__field:last-child { border-bottom: none; }
.detail__field-label {
  font-size: 0.85em;
  color: #888;
  margin-bottom: 0.25em;
}
.detail__field-value {
  font-size: 1em;
  margin-bottom: 0.25em;
}
.detail__field-provenance {
  display: flex;
  align-items: center;
  gap: 0.5em;
}
.provenance-badge {
  display: inline-block;
  font-size: 0.75em;
  padding: 0.15em 0.5em;
  border-radius: 4px;
  background: #eee;
  color: #666;
}
</style>
