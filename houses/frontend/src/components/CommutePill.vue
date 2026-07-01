<script setup lang="ts">
import { computed } from 'vue'

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
  const good = props.goodMax ?? 45
  const fine = props.fineMax ?? 75
  if (d === null) return 'pill--muted'
  if (d <= good) return 'pill--good'
  if (d <= fine) return 'pill--warn'
  return 'pill--bad'
})

const displayText = computed(() => {
  const durStr = formatDuration(props.duration)
  const modeStr = props.mode ? ` ${props.mode}` : ''
  const costStr = props.cost ? ` · £${props.cost.toFixed(2)}` : ''
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
  <span class="pill" :class="colour">
    {{ displayText }}
  </span>
</template>

<style scoped>
.pill {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.6;
  white-space: nowrap;
}
.pill--good { background: var(--green-bg); color: var(--green); }
.pill--warn { background: var(--orange-bg); color: var(--orange); }
.pill--bad { background: var(--red-bg); color: var(--red); }
.pill--muted { background: var(--muted-bg); color: var(--muted); }
</style>
