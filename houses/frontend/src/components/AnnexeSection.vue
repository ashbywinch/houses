<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import * as api from '../services/api'
import { usePropertiesStore } from '../stores/properties'

const props = defineProps<{
  rid: string
  annexe?: {
    address: string
    band: string
    yearly_cost?: { value: { amount: string; currency: string } } | null
  } | null
  payers: string[]
  ignored: boolean
  adults: { name: string }[]
}>()

const store = usePropertiesStore()
const saving = ref(false)
const errorMsg = ref('')
const localPayers = ref<string[]>([])
const localIgnored = ref(false)

watch(
  () => [props.payers, props.ignored],
  () => {
    localPayers.value = [...props.payers]
    localIgnored.value = props.ignored
  },
  { immediate: true },
)

const yearlyText = computed(() => {
  const c = props.annexe?.yearly_cost?.value
  return c ? `£${Number(c.amount).toLocaleString()}/yr` : ''
})

function togglePayer(name: string) {
  if (localPayers.value.includes(name)) {
    localPayers.value = localPayers.value.filter(p => p !== name)
  } else {
    localPayers.value = [...localPayers.value, name]
  }
}

async function save() {
  saving.value = true
  errorMsg.value = ''
  try {
    await api.patchAnnexe(props.rid, { payers: localPayers.value, ignored: localIgnored.value })
    await store.loadDetail(props.rid, true)
  } catch {
    errorMsg.value = "Couldn't save the annexe choice."
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
  <section id="section-annexe" class="detail-section">
    <h2 class="detail-section__title">Annexe — separate council tax</h2>

    <template v-if="annexe && !localIgnored">
      <p class="annexe-intro">
        {{ annexe.address }} is a separate dwelling at this address with its own council tax
        bill (Band {{ annexe.band }}{{ yearlyText ? ' · ' + yearlyText : '' }}).
        <template v-if="localPayers.length === 0">
          It is <strong>not yet included</strong> in the monthly costs — pick who pays a share below.
        </template>
      </p>
      <p class="annexe-question">Who should pay a share of the annexe council tax?</p>
      <label v-for="a in adults" :key="a.name" class="annexe-payer">
        <input
          type="checkbox"
          :checked="localPayers.includes(a.name)"
          @change="togglePayer(a.name)"
        />
        {{ a.name }}
      </label>
      <div class="annexe-actions">
        <button class="btn--primary" :disabled="saving" @click="save">
          {{ saving ? 'Saving…' : 'Save' }}
        </button>
        <button class="btn--secondary" :disabled="saving" @click="hideAnnexe">
          Not related — hide
        </button>
      </div>
      <p v-if="errorMsg" class="annexe-error">{{ errorMsg }}</p>
    </template>

    <template v-else-if="annexe && localIgnored">
      <p class="annexe-intro">
        Annexed address hidden ({{ annexe.address }}) — treated as unrelated to this property.
        <button class="annexe-restore" @click="restoreAnnexe">Show it again</button>
      </p>
    </template>
  </section>
</template>

<style scoped>
.annexe-intro {
  margin: 0 0 0.5rem;
}
.annexe-question {
  font-weight: var(--fw-semibold);
  margin: 0.5rem 0;
}
.annexe-payer {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.2rem 0;
}
.annexe-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
.annexe-error {
  color: var(--red);
  font-size: 0.85rem;
  margin: 0.4rem 0 0;
}
.annexe-restore {
  background: none;
  border: none;
  color: var(--blue);
  cursor: pointer;
  padding: 0;
  margin-left: 0.25rem;
  text-decoration: underline;
}
</style>
