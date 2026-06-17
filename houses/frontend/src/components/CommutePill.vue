<script setup lang="ts">
defineProps<{
  label: string
  duration: number | null
  cost: number | null
  goodMax?: number
  fineMax?: number
}>()

function colourClass(duration: number | null, good = 30, fine = 45): string {
  if (duration === null) return 'pill--muted'
  if (duration <= good) return 'pill--good'
  if (duration <= fine) return 'pill--warn'
  return 'pill--bad'
}

function formatDuration(minutes: number | null): string {
  if (minutes === null) return '?'
  if (minutes < 60) return `${minutes}m`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m > 0 ? `${h}h ${m}m` : `${h}h`
}

function formatCost(cost: number | null): string {
  if (cost === null) return ''
  return `£${cost.toFixed(2)}`
}
</script>

<template>
  <span
    class="pill"
    :class="colourClass(duration, goodMax, fineMax)"
  >
    {{ label }} {{ formatDuration(duration) }}{{ cost ? ' · ' + formatCost(cost) : '' }}
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
