<script setup lang="ts">
import { computed } from 'vue'
import { usePropertiesStore } from '../stores/properties'
import { commuteColour } from '../formatters/commute'

const store = usePropertiesStore()
const props = defineProps<{
  label: string
  duration: number | null
  cost?: number | null
  mode?: string
  goodMax?: number
  fineMax?: number
}>()
const colour = computed(() => {
  const d = props.duration
  if (d === null) return 'pill--muted'
  const c = commuteColour(
    d,
    props.goodMax ?? store.settings.commute_thresholds?.good ?? 45,
    props.fineMax ?? store.settings.commute_thresholds?.warn ?? 75,
  )
  if (c === 'green') return 'pill--good'
  if (c === 'orange') return 'pill--warn'
  return 'pill--bad'
})

const displayText = computed(() => {
  const durStr = formatDuration(props.duration)
  const modeStr = props.mode ? ` ${props.mode}` : ''
  const costStr = props.cost != null ? ` · £${props.cost.toFixed(2)}` : ''
  if (props.label) return `${props.label} ${durStr}${modeStr}${costStr}`
  return `${durStr}${modeStr}${costStr}`
})

function formatDuration(minutes: number | null): string {
  if (minutes === null) return '?'
  if (minutes < 60) return `${minutes}m`
  const h = Math.floor(minutes / 60)
  const r = minutes % 60
  if (r === 0) return `${h}h`
  return `${h}h${r}`
}
</script>

<template>
  <span class="pill" :class="colour" :title="duration === null ? 'No route found for this commute' : undefined">
    {{ displayText }}
  </span>
</template>

<style scoped>
.pill {
  display: inline-flex;
  align-items: center;
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.6;
  white-space: nowrap;
  min-height: 44px;
}
.pill--good { background: var(--green); color: #fff; }
.pill--warn { background: var(--orange); color: #fff; }
.pill--bad { background: var(--red); color: #fff; }
.pill--muted { background: var(--commute-none); color: #fff; }
</style>
