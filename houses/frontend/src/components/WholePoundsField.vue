<script setup lang="ts">
/** Whole-pounds money input — non-digits are blocked at the keystroke
 *  and paste level; any leak reverts the field whole (never truncated).
 *  Shared by the what-if panel and the settings page. The value is a
 *  display string; listeners fall through to the native <input> so
 *  surrounding autosave/change handlers keep working. */
import { blockWholePoundsKey, rejectWholePoundsPaste, wholePoundsValue } from '../formatters/money'

const props = defineProps<{ modelValue: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>()

function onInput(e: Event) {
  const el = e.target as HTMLInputElement
  emit('update:modelValue', wholePoundsValue(el, props.modelValue))
}
</script>

<template>
  <input
    type="text"
    inputmode="numeric"
    :value="modelValue"
    @keydown="blockWholePoundsKey"
    @paste="rejectWholePoundsPaste"
    @input="onInput"
  />
</template>
