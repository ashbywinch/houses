<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

export interface MapMarker {
  lat: number
  lon: number
  label: string
  url?: string
  color?: string
}

export interface MapPolygon {
  coords: [number, number][]
  name?: string
  url?: string
}

export interface MapLayer {
  name: string
  color: string
  fillOpacity?: number
  weight?: number
  polygons: MapPolygon[]
}

const props = withDefaults(defineProps<{
  markers: MapMarker[]
  layers?: MapLayer[]
  height?: number
}>(), {
  layers: () => [],
})

const emit = defineEmits<{ error: [] }>()

const container = ref<HTMLDivElement | null>(null)
let map: L.Map | null = null
const overlayGroups: L.LayerGroup[] = []

function buildLayers() {
  for (const g of overlayGroups) {
    g.clearLayers()
  }
  while (overlayGroups.length) overlayGroups.pop()

  for (const layer of props.layers) {
    const group = L.layerGroup()
    for (const poly of layer.polygons) {
      if (!poly.coords?.length) continue
      const latlngs = poly.coords.map(([lat, lon]) => [lat, lon] as [number, number])
      const polygon = L.polygon(latlngs, {
        color: layer.color,
        fillColor: layer.color,
        fillOpacity: layer.fillOpacity ?? 0.12,
        weight: layer.weight ?? 2,
      })
      const popupParts: string[] = []
      if (poly.name) popupParts.push(`<strong>${escapeHtml(poly.name)}</strong>`)
      if (poly.url) popupParts.push(`<a href="${escapeAttr(poly.url)}" target="_blank" rel="noopener">View search</a>`)
      if (popupParts.length) polygon.bindPopup(popupParts.join('<br>'))
      polygon.addTo(group)
    }
    group.addTo(map!)
    overlayGroups.push(group)
  }
}

function buildMarkers() {
  const group = L.layerGroup()
  for (const m of props.markers) {
    const marker = L.circleMarker([m.lat, m.lon], {
      radius: 8,
      color: '#fff',
      weight: 2,
      fillColor: m.color ?? '#2563eb',
      fillOpacity: 1,
    })
    const label = escapeHtml(m.label)
    const content = m.url
      ? `<a href="${escapeAttr(m.url)}">${label}</a>`
      : `<strong>${label}</strong>`
    marker.bindPopup(content)
    marker.addTo(group)
  }
  group.addTo(map!)
  overlayGroups.push(group)
}

function fitBounds() {
  const points: [number, number][] = []
  for (const m of props.markers) points.push([m.lat, m.lon])
  for (const layer of props.layers) {
    for (const poly of layer.polygons) {
      for (const [lat, lon] of poly.coords) points.push([lat, lon])
    }
  }
  if (!points.length) return
  const bounds = L.latLngBounds(points)
  map!.fitBounds(bounds.pad(0.08))
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
function escapeAttr(s: string): string {
  return escapeHtml(s).replace(/"/g, '&quot;')
}

onMounted(() => {
  if (!container.value) return
  try {
    map = L.map(container.value, { scrollWheelZoom: false })
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map)
    buildLayers()
    buildMarkers()
    fitBounds()
    map.invalidateSize()
  } catch (e) {
    console.error('Map init failed:', e)
    emit('error')
  }
})

watch(
  () => [props.markers, props.layers],
  () => {
    if (!map) return
    buildLayers()
    buildMarkers()
    fitBounds()
  },
  { deep: true },
)

onBeforeUnmount(() => {
  if (map) {
    map.remove()
    map = null
  }
})
</script>

<template>
  <div
    ref="container"
    class="map-view"
    :style="props.height ? { height: props.height + 'px' } : undefined"
  />
</template>

<style scoped>
.map-view {
  width: 100%;
  border-radius: 12px;
  z-index: 0;
}
</style>
