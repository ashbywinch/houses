<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

export type MapMarkerKind = 'price' | 'house' | 'school'

export interface MapMarker {
  lat: number
  lon: number
  label: string
  url?: string
  color?: string
  /** How the pin renders: a price chip, a house icon, or a school icon. */
  kind?: MapMarkerKind
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
let overlayControl: L.Control.Layers | null = null
const overlayGroups = new Map<string, L.LayerGroup>()

const HOUSE_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="24" height="24" aria-hidden="true"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9.5 21v-6h5v6"/></svg>`
const SCHOOL_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="24" height="24" aria-hidden="true"><path d="M22 9 12 4 2 9l10 5 10-5z"/><path d="M6 11.5V17c0 1.5 2.7 3 6 3s6-1.5 6-3v-5.5"/><path d="M22 9v6"/></svg>`

function buildLayers() {
  for (const g of overlayGroups.values()) g.clearLayers()
  overlayGroups.clear()

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
    overlayGroups.set(layer.name, group)
  }

  // The checkbox key (like the toolchain map): one checkbox per layer.
  if (overlayControl) {
    map!.removeControl(overlayControl)
  }
  const overlays: Record<string, L.LayerGroup> = {}
  for (const [name, group] of overlayGroups) {
    overlays[name] = group
    group.addTo(map!)
  }
  overlayControl = L.control.layers({}, overlays, { collapsed: false }).addTo(map!)
}

function markerIcon(m: MapMarker): L.DivIcon {
  const kind = m.kind ?? 'price'
  const color = m.color ?? '#2563eb'
  if (kind === 'house') {
    return L.divIcon({
      className: 'mapview-icon',
      html: `<span class="mapview-icon__svg" style="color:${color}">${HOUSE_ICON}</span>`,
      iconSize: [28, 28],
      iconAnchor: [14, 24],
      popupAnchor: [0, -24],
    })
  }
  if (kind === 'school') {
    return L.divIcon({
      className: 'mapview-icon',
      html: `<span class="mapview-icon__svg" style="color:${color}">${SCHOOL_ICON}</span>`,
      iconSize: [26, 26],
      iconAnchor: [13, 23],
      popupAnchor: [0, -23],
    })
  }
  // price chip — the label sits above the point, centered (as the old
  // absolute-positioned pin labels did)
  const label = escapeHtml(m.label)
  const inner = m.url
    ? `<a class="mapview-price" href="${escapeAttr(m.url)}" style="border-color:${color}">${label}</a>`
    : `<span class="mapview-price" style="border-color:${color}">${label}</span>`
  return L.divIcon({
    className: 'mapview-price-wrap',
    html: inner,
    iconSize: [0, 0],
    iconAnchor: [0, 0],
  })
}

function buildMarkers() {
  const group = L.layerGroup()
  for (const m of props.markers) {
    const marker = L.marker([m.lat, m.lon], { icon: markerIcon(m) })
    const label = escapeHtml(m.label)
    const content = m.url
      ? `<a href="${escapeAttr(m.url)}">${label}</a>`
      : `<strong>${label}</strong>`
    marker.bindPopup(content)
    marker.addTo(group)
  }
  group.addTo(map!)
  overlayGroups.set('__markers__', group)
}

function fitBounds() {
  // Starting view = the properties (markers), as the old iframe bbox was
  // property-centred. The isochrones are overlays — the user pans/zooms
  // to them via the key or the zoom controls.
  const points: [number, number][] = props.markers.map(m => [m.lat, m.lon])
  if (!points.length) return
  const bounds = L.latLngBounds(points)
  map!.fitBounds(bounds.pad(0.2))
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
.map-view :deep(.mapview-price-wrap) {
  background: none;
  border: none;
}
.map-view :deep(.mapview-price) {
  display: block;
  background: var(--card-bg);
  color: var(--text);
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  padding: 2px 8px;
  border-radius: 6px;
  border: 2px solid var(--blue);
  white-space: nowrap;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.2);
  line-height: 1.4;
  text-decoration: none;
  transform: translate(-50%, -100%);
}
.map-view :deep(.mapview-icon__svg) {
  display: block;
  filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.4));
}
.map-view :deep(.leaflet-control-layers) {
  border-radius: 8px;
  font-size: var(--fs-xs);
}
</style>
