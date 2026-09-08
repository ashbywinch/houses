<script setup lang="ts">
/** One monthly money line: a label and its figure — the shared render
 *  for the index cards and the property detail header.
 *
 *  Two figure modes, matching what the server sends:
 *  - ``delta``: the signed monthly increment vs the current home
 *    (``delta_vs_home`` — per group, explicit sign, whole pounds).
 *  - ``absolute``: today's total (the current home itself, or no
 *    baseline). Falls back to a dash when neither is computable.
 *
 *  The label is server data (the DAG's joint-owner names) — never
 *  hard-coded here.
 */
import { computed } from 'vue'
import { signedPounds } from '../formatters/money'

const props = defineProps<{
  label: string
  delta?: { value: string; approx: boolean } | null
  absolute?: number | null
  approx?: boolean
  lineClass?: string
  title?: string
}>()

const figure = computed(() => {
  if (props.delta) return `${props.delta.approx ? '≈' : ''}${signedPounds(props.delta.value)}/mo`
  if (props.absolute !== null && props.absolute !== undefined) {
    return `${props.approx ? '≈' : ''}£${props.absolute.toLocaleString()}/mo`
  }
  return '£—/mo'
})
</script>

<template>
  <span :class="lineClass" :title="title">
    <strong>{{ label }}</strong>
    {{ figure }}
  </span>
</template>
