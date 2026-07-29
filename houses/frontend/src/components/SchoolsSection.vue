<script setup lang="ts">
import { ofstedClass } from '../formatters/format'
import { schoolWalkMin } from '../formatters/school'

defineProps<{
  schools: any
  commutes: any
}>()

function getSchoolWalkMinutes(commutes: any, labelPart: string): { value: number; unit: string } | null {
  if (!commutes) return null
  for (const [key, v] of Object.entries(commutes)) {
    if (!key.includes(labelPart)) continue
    const val = (v as any)?.value as any
    if (!val?.is_child) continue
    const dur = val.duration as { value: number; unit: string } | undefined
    return dur ? { value: Math.round(dur.value), unit: 'minute' } : null
  }
  return null
}
</script>

<template>
  <section id="section-schools" class="detail-section">
    <h2 class="detail-section__title">Schools</h2>

    <div v-if="schools?.primary?.school?.succeeded" class="detail-field">
      <span class="detail-field__label">Primary</span>
      <div class="detail-field__value">
        <a :href="schools.primary.school.value?.url" target="_blank">{{ schools.primary.school.value?.name }}</a>
        <span class="pill pill--sm" :class="ofstedClass(schools.primary.school.value?.ofsted)">{{ schools.primary.school.value?.ofsted }}</span>
        <span v-if="getSchoolWalkMinutes(commutes, 'Primary')" class="pill pill--sm pill--good">{{ schoolWalkMin(getSchoolWalkMinutes(commutes, 'Primary')) }}</span>
      </div>
    </div>
    <div v-if="schools?.secondary?.school?.succeeded" class="detail-field">
      <span class="detail-field__label">Secondary</span>
      <div class="detail-field__value">
        <a :href="schools.secondary.school.value?.url" target="_blank">{{ schools.secondary.school.value?.name }}</a>
        <span class="pill pill--sm" :class="ofstedClass(schools.secondary.school.value?.ofsted)">{{ schools.secondary.school.value?.ofsted }}</span>
        <span v-if="getSchoolWalkMinutes(commutes, 'Secondary')" class="pill pill--sm pill--good">{{ schoolWalkMin(getSchoolWalkMinutes(commutes, 'Secondary')) }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.detail-section {
  padding: 16px;
  border-bottom: 8px solid var(--page-bg);
}
.detail-section__title {
  font-size: 16px; font-weight: 700; margin: 0 0 12px;
}
.detail-field {
  display: flex; flex-wrap: wrap; align-items: baseline;
  gap: 8px; padding: 6px 0;
}
.detail-field__label { font-size: 13px; font-weight: 600; color: var(--text-secondary); min-width: 80px; }
.detail-field__value { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 14px; }
.pill { display: inline-flex; align-items: center; padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; line-height: 1.6; white-space: nowrap; }
.pill--sm { font-size: 11px; padding: 1px 7px; }
.pill--good { background: var(--green-bg); color: var(--green); }
</style>
