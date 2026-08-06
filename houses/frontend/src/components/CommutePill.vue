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
  const cap = props.cost != null && props.cost >= 100
  const costStr = props.cost != null ? ` · £${props.cost.toFixed(2)}${cap ? ' (max)' : ''}` : ''
  if (props.label) return `${props.label} ${durStr}${modeStr}${costStr}`
  return `${durStr}${modeStr}${costStr}`
})

const titleText = computed(() => {
  if (props.duration === null) return 'No route found for this commute'
  if (props.cost != null && props.cost >= 100) return '£100.00 is the TfL daily maximum, not the actual fare'
  return undefined
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
  <span class="pill" :class="colour" :title="titleText">
    {{ displayText }}
  </span>
</template>

<style scoped>
.pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: var(--radius-full);
  font-size: var(--fs-sm);
  font-weight: var(--fw-medium);
  line-height: 1.4;
  white-space: nowrap;
}
.pill--good { background: var(--green); color: #fff; }
.pill--warn { background: var(--orange); color: #fff; }
.pill--bad { background: var(--red); color: #fff; }
.pill--muted { background: var(--commute-none); color: #fff; }
</style>
