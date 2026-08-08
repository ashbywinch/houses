<script setup lang="ts">
/** A switch toggle — shared by the settings page and the what-if panel
 *  (has-a-car, selling-a-home). Accessible: role=switch + aria-checked. */
const props = withDefaults(defineProps<{ modelValue?: boolean; disabled?: boolean }>(), { modelValue: false })
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'change'): void
}>()
</script>

<template>
  <button
    type="button"
    role="switch"
    :aria-checked="props.modelValue"
    :class="['switch', { 'switch--on': props.modelValue }]"
    :disabled="disabled"
    @click="emit('update:modelValue', !props.modelValue); emit('change')"
  >
    <span class="switch__track"><span class="switch__knob" /></span>
  </button>
</template>
