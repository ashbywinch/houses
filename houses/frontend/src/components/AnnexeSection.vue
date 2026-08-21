<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import * as api from '../services/api'
import { usePropertiesStore } from '../stores/properties'

const props = defineProps<{
  rid: string
  mainBill?: { band: string; yearly_cost?: { value: { amount: string; currency: string } } | null } | null
  annexe?: {
    address: string
    band: string
    yearly_cost?: { value: { amount: string; currency: string } } | null
  } | null
  mainPayers: string[]
  annexePayers: string[]
  ignored: boolean
  adults: { name: string }[]
}>()

const store = usePropertiesStore()
const saving = ref(false)
const errorMsg = ref('')
const localMainPayers = ref<string[]>([])
const localAnnexePayers = ref<string[]>([])
const localIgnored = ref(false)

watch(
  () => [props.mainPayers, props.annexePayers, props.ignored],
  () => {
    localMainPayers.value = [...props.mainPayers]
    localAnnexePayers.value = [...props.annexePayers]
    localIgnored.value = props.ignored
  },
  { immediate: true },
)

const mainYearlyText = computed(() => {
  const c = props.mainBill?.yearly_cost?.value
  return c ? `£${Number(c.amount).toLocaleString()}/yr` : ''
})

const annexeYearlyText = computed(() => {
  const c = props.annexe?.yearly_cost?.value
  return c ? `£${Number(c.amount).toLocaleString()}/yr` : ''
})

/** Empty main payers = all adults (the default headcount split). */
const mainPayerNames = computed(() =>
  localMainPayers.value.length > 0 ? localMainPayers.value : props.adults.map(a => a.name),
)

function toggle(list: string[], name: string): string[] {
  return list.includes(name) ? list.filter(p => p !== name) : [...list, name]
}

async function save() {
  saving.value = true
  errorMsg.value = ''
  try {
    await api.patchCouncilTax(props.rid, {
      main_payers: localMainPayers.value,
      annexe_payers: localAnnexePayers.value,
      ignored: localIgnored.value,
    })
    await store.loadDetail(props.rid, true)
  } catch {
    errorMsg.value = "Couldn't save the council tax choice."
  } finally {
    saving.value = false
  }
}

function hideAnnexe() {
  localIgnored.value = true
  save()
}

function restoreAnnexe() {
  localIgnored.value = false
  save()
}
</script>

<template>
  <section id="section-council-tax" class="detail-section">
    <h2 class="detail-section__title">Council tax — who pays</h2>

    <div class="ctax-bill">
      <p class="ctax-bill__head">
        Main house — Band {{ mainBill?.band }}{{ mainYearlyText ? ' · ' + mainYearlyText : '' }}
      </p>
      <p class="ctax-bill__hint">
        Split between: <strong>{{ mainPayerNames.join(', ') }}</strong>
        <template v-if="localMainPayers.length === 0"> (all adults — default)</template>
      </p>
      <label v-for="a in adults" :key="'m' + a.name" class="ctax-payer">
        <input type="checkbox" :checked="localMainPayers.includes(a.name)" @change="localMainPayers = toggle(localMainPayers, a.name)" />
        {{ a.name }}
      </label>
    </div>

    <template v-if="annexe && !localIgnored">
      <div class="ctax-bill ctax-bill--annexe">
        <p class="ctax-bill__head">
          Annexe — {{ annexe.address }} · Band {{ annexe.band }}{{ annexeYearlyText ? ' · ' + annexeYearlyText : '' }}
          — separate council tax
        </p>
        <p class="ctax-bill__hint">
          <template v-if="localAnnexePayers.length === 0">
            Not yet included in the monthly costs — pick who pays a share below.
          </template>
          <template v-else>Split between: <strong>{{ localAnnexePayers.join(', ') }}</strong></template>
        </p>
        <label v-for="a in adults" :key="'a' + a.name" class="ctax-payer">
          <input type="checkbox" :checked="localAnnexePayers.includes(a.name)" @change="localAnnexePayers = toggle(localAnnexePayers, a.name)" />
          {{ a.name }}
        </label>
        <button class="ctax-hide" :disabled="saving" @click="hideAnnexe">Not related — hide</button>
      </div>
    </template>
    <template v-else-if="annexe && localIgnored">
      <p class="ctax-bill__hint">
        Annexed address hidden ({{ annexe.address }}) — treated as unrelated.
        <button class="ctax-restore" @click="restoreAnnexe">Show it again</button>
      </p>
    </template>

    <div class="ctax-actions">
      <button class="btn--primary" :disabled="saving" @click="save">
        {{ saving ? 'Saving…' : 'Save' }}
      </button>
    </div>
    <p v-if="errorMsg" class="ctax-error">{{ errorMsg }}</p>
  </section>
</template>

<style scoped>
.ctax-bill {
  margin: 0.25rem 0 0.75rem;
}
.ctax-bill--annexe {
  border-top: 1px solid var(--border);
  padding-top: 0.75rem;
}
.ctax-bill__head {
  font-weight: var(--fw-semibold);
  margin: 0 0 0.25rem;
}
.ctax-bill__hint {
  margin: 0 0 0.5rem;
  color: var(--text-secondary);
}
.ctax-payer {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.15rem 0;
}
.ctax-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.5rem;
}
.ctax-hide {
  background: none;
  border: none;
  color: var(--blue);
  cursor: pointer;
  padding: 0.35rem 0 0;
  text-decoration: underline;
}
.ctax-restore {
  background: none;
  border: none;
  color: var(--blue);
  cursor: pointer;
  padding: 0;
  margin-left: 0.25rem;
  text-decoration: underline;
}
.ctax-error {
  color: var(--red);
  font-size: 0.85rem;
  margin: 0.4rem 0 0;
}
</style>
