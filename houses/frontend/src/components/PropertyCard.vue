<script setup lang="ts">
import type { PropertyResponse } from '../types'
import CommutePill from './CommutePill.vue'

const props = defineProps<{
  rid: string
  data: PropertyResponse
}>()

const address = props.data.best_address.succeeded
  ? props.data.best_address.value
  : props.rid

const location = props.data.best_location.succeeded
  ? props.data.best_location.value
  : null

const mapUrl = location
  ? `https://www.google.com/maps?q=${location.lat},${location.lon}`
  : null

const rightmoveUrl = `https://www.rightmove.co.uk/properties/${props.rid}`
</script>

<template>
  <article class="card" :class="{ 'card--slim': !location }">
    <div class="card__border" :class="data.best_location.succeeded ? 'card__border--current' : 'card__border--dismissed'" />
    <div class="card__body">
      <div class="card__header-row">
        <a
          :href="'#/property/' + rid"
          class="card__address"
        >{{ address }}</a>
        <a
          v-if="mapUrl"
          :href="mapUrl"
          target="_blank"
          class="card__map-link"
          title="Open in Google Maps"
        >🗺</a>
        <a
          :href="rightmoveUrl"
          target="_blank"
          class="card__external-link"
          title="Open on Rightmove"
        >↗</a>
      </div>
      <div class="card__specs">
        RID: {{ rid }}
      </div>
      <div v-if="location" class="card__commutes">
        <span class="card__metric-label">Commute</span>
        <CommutePill
          :label="'Simon'"
          :duration="30"
          :cost="4.50"
        />
      </div>
    </div>
  </article>
</template>

<style scoped>
.card {
  position: relative;
  background: var(--card-bg);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}
.card--slim .card__body { padding-bottom: 10px; }
.card__border {
  position: absolute;
  top: 0;
  left: 0;
  width: 4px;
  height: 100%;
  border-radius: var(--radius) 0 0 var(--radius);
}
.card__border--current { background: var(--green); }
.card__border--dismissed { background: var(--red); }
.card__body {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.card__header-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.card__address {
  flex: 1;
  min-width: 0;
  font-size: 15px;
  font-weight: 600;
  color: #1565c0;
  text-decoration: underline;
  text-decoration-color: rgba(21, 101, 192, 0.3);
  word-break: break-word;
}
.card__address:hover { text-decoration-color: #1565c0; }
.card__map-link {
  font-size: 14px;
  text-decoration: none;
  line-height: 1;
  opacity: 0.6;
}
.card__map-link:hover { opacity: 1; }
.card__external-link {
  font-size: 13px;
  text-decoration: none;
  color: var(--text-muted);
  line-height: 1;
  padding: 2px;
}
.card__external-link:hover { color: var(--text); }
.card__specs {
  font-size: 13px;
  color: var(--text-secondary);
}
.card__commutes { line-height: 1.6; }
.card__metric-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  margin-right: 4px;
}
</style>
