<script setup lang="ts">
import { computed, ref } from 'vue'
import ProvenanceToggle from './ProvenanceToggle.vue'
import { epcClass } from '../formatters/format'
import type { Provenance } from '../types'
import {
  blockWholePoundsKey,
  rejectWholePoundsPaste,
  wholePoundsValue,
} from '../formatters/money'
import { patchRentalIncome, patchWorksEstimate } from '../services/api'
import { usePropertiesStore } from '../stores/properties'

const props = defineProps<{
  affordability: any
  epc: any
  persons?: any
  rid?: string
  currentPerson?: string | null
}>()

function epcStepClass(band: string): string {
    const g = epcClass(band)
    return g ? `epc-step--${g}` : ''
}

// ── Provenance toggle state (one at a time) ────────────

function isImpossible(val: any): boolean {
  return val && !val.succeeded && val.error != null
}

// ── Part A: uncertainty rendering ──────────────────────

/** True when the total is approximate (stddev > 0). */
const totalMonthlyApprox = computed(() => {
  const m = props.affordability?.group_monthly_cost
  return !!m?.succeeded && ((m.value?.couple?.stddev ?? 0) > 0)
})

const fmt = (n: number | undefined): string =>
  n == null ? '' : `${n < 0 ? '−' : ''}£${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`

/** Zero values are omitted — a row only appears when the number is
 *  non-zero. */
function row(
  label: string,
  value: number | undefined,
  provenance?: Provenance,
): { label: string; value: string; provenance?: Provenance } | null {
  if (value == null || value === 0) return null
  return { label, value: fmt(value), provenance }
}

/** Row labels for the joint-owners' breakdown — mortgage and rental
 *  income belong to them; council tax, sinking fund and commutes split
 *  by group. Each row carries the provenance of its DAG component so
 *  the ⓘ explains exactly that figure. */
function coupleRows() {
  const b = props.affordability?.group_monthly_cost?.value?.couple_breakdown
  if (!b) return []
  const a = props.affordability ?? {}
  return [
    row('Mortgage', b.mortgage, a.monthly_mortgage?.provenance),
    row('Council tax', b.council_tax, a.council_tax?.provenance),
    row('Sinking fund', b.sinking_fund, a.monthly_sinking_fund?.provenance),
    row('Commutes', b.commutes, a.monthly_commute_cost?.provenance),
    row('Life insurance', b.insurance, a.life_insurance_total?.provenance),
    row('Rental income', b.rental_income, a.rental_income?.provenance),
    row('Rent paid', b.rent_paid, a.group_monthly_cost?.provenance),
  ].filter((r): r is { label: string; value: string; provenance?: Provenance } => r !== null)
}

/** Row labels for the other adults' breakdown — their rent paid is their
 *  own cost; council tax, sinking fund and commutes split by group. */
function othersRows() {
  const b = props.affordability?.group_monthly_cost?.value?.others_breakdown
  if (!b) return []
  const a = props.affordability ?? {}
  return [
    row('Council tax', b.council_tax, a.council_tax?.provenance),
    row('Sinking fund', b.sinking_fund, a.monthly_sinking_fund?.provenance),
    row('Commutes', b.commutes, a.monthly_commute_cost?.provenance),
    row('Life insurance', b.insurance, a.life_insurance_total?.provenance),
    row('Rent paid', b.rent_paid, a.group_monthly_cost?.provenance),
  ].filter((r): r is { label: string; value: string; provenance?: Provenance } => r !== null)
}

/** When the total can't be calculated, name the leaf reason the UI
 *  knows (e.g. "Works estimate required for: Ashby"). */
const totalBlockedReason = computed(() => {
  const t = props.affordability?.group_monthly_cost
  if (!t || t.succeeded || t.error == null) return ''
  const detail = (t as { error_detail?: { user_message?: string } }).error_detail
  return detail?.user_message || t.error || ''
})

// ── Works estimate inline editing ─────────────────────
const editingPerson = ref<string | null>(null)
const editValue = ref<string>('')
const store = usePropertiesStore()

function startEdit(person: string, currentValue: number | null) {
  editingPerson.value = person
  editValue.value = currentValue != null ? String(currentValue) : ''
}

function cancelEdit() {
  editingPerson.value = null
  editValue.value = ''
}

async function saveEdit(person: string) {
  editingPerson.value = null
  const parsed = editValue.value === '' ? null : Number(editValue.value)
  if (isNaN(parsed as number) && editValue.value !== '') return
  if (!props.rid) return
  await patchWorksEstimate(props.rid, person, parsed as number | null)
  if (props.rid) {
    await store.loadDetail(props.rid, true)
  }
}

function handleKeydown(e: KeyboardEvent, person: string) {
  // Whole-pounds rule first (blocks '.'/'e'/sign), then Enter/Escape.
  blockWholePoundsKey(e)
  if (e.key === 'Enter') saveEdit(person)
  else if (e.key === 'Escape') cancelEdit()
}

// ── Rental income inline editing ──────────────────────
const editingRental = ref(false)
const editRentalValue = ref<string>('')

function startEditRental() {
  const current = props.affordability?.rental_income?.value?.amount
  editRentalValue.value = current != null ? String(current) : '0'
  editingRental.value = true
}

function cancelEditRental() {
  editingRental.value = false
  editRentalValue.value = ''
}

async function saveRental() {
  editingRental.value = false
  const parsed = editRentalValue.value === '' ? null : Number(editRentalValue.value)
  if (isNaN(parsed as number) && editRentalValue.value !== '') return
  if (!props.rid) return
  await patchRentalIncome(props.rid, parsed as number | null)
  if (props.rid) {
    await store.loadDetail(props.rid, true)
  }
}

function handleRentalKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') saveRental()
  else if (e.key === 'Escape') cancelEditRental()
}

// ── Helpers for works display ─────────────────────────
// Per-person works estimates are Money-shaped ({amount, currency}) —
// normalize to a plain number for the editing UI. This is an editor,
// not a provenance renderer; display goes through ProvenanceView.
const worksEstimates = (): Record<string, number> => {
  const raw = props.affordability?.works_estimates?.succeeded
    ? (props.affordability.works_estimates.value as Record<string, unknown> ?? {})
    : {}
  const out: Record<string, number> = {}
  for (const [name, v] of Object.entries(raw)) {
    if (v == null) continue
    if (typeof v === 'number') { out[name] = v; continue }
    if (typeof v === 'object' && 'amount' in (v as object)) {
      out[name] = Number((v as { amount: string }).amount)
    }
  }
  return out
}

const buyerList = () =>
  props.persons?.value
    ? (props.persons.value as any[]).filter((p: any) => !p.is_child)
    : []

function canEdit(personName: string): boolean {
  return props.currentPerson != null && props.currentPerson === personName
}
</script>

<template>
  <section id="section-costs" class="detail-section">
    <h2 class="detail-section__title">Costs</h2>

    <div class="costs-table">
      <!-- Cost of Works (editable per-person input — not part of the
           monthly group breakdown) -->
      <div class="costs-row" :class="{ 'costs-row--impossible': isImpossible(affordability?.total_works) }">
        <span class="costs-label">Cost of Works</span>
        <span v-if="affordability?.total_works?.succeeded && affordability?.total_works?.value" class="costs-value">£{{ affordability.total_works.value.amount }}</span>
        <span v-else-if="isImpossible(affordability?.total_works)" class="costs-value costs-value--impossible">£? — required</span>
        <span v-else class="costs-value">?</span>
      </div>
      <!-- Per-person works breakdown -->
      <div v-if="affordability?.works_estimates?.succeeded" class="costs-subsection">
        <div
          v-for="p in buyerList()"
          :key="String(p.name)"
          class="costs-row costs-row--sub"
        >
          <span class="costs-label">{{ p.name }}</span>
          <!-- Editing state (current person only) -->
          <div v-if="editingPerson === p.name" class="costs-edit-group">
            <span class="costs-edit-prefix">£</span>
            <input
              :value="editValue"
              type="text"
              inputmode="numeric"
              class="costs-edit-input"
              autofocus
              @keydown="handleKeydown($event, p.name as string)"
              @paste="rejectWholePoundsPaste"
              @input="editValue = wholePoundsValue($event.target as HTMLInputElement, editValue)"
              @blur="saveEdit(p.name as string)"
            />
          </div>
          <!-- Editable value (current person) -->
          <span
            v-else-if="canEdit(p.name as string) && p.name in worksEstimates() && worksEstimates()[p.name as string] != null"
            class="costs-value costs-value--editable"
            title="Click to edit your works estimate"
            @click="startEdit(p.name as string, worksEstimates()[p.name as string])"
          >✎ £{{ worksEstimates()[p.name as string].toLocaleString() }}</span>
          <span
            v-else-if="canEdit(p.name as string) && (p as any).works_estimate_required"
            class="costs-value costs-value--editable costs-value--required"
            title="Click to add your works estimate"
            @click="startEdit(p.name as string, null)"
          >✎ £? — required</span>
          <span
            v-else-if="canEdit(p.name as string)"
            class="costs-value costs-value--editable"
            title="Click to add your works estimate"
            @click="startEdit(p.name as string, null)"
          >✎ £?</span>
          <!-- Read-only value (other person) -->
          <span
            v-else-if="p.name in worksEstimates() && worksEstimates()[p.name as string] != null"
            class="costs-value costs-value--readonly"
          >£{{ worksEstimates()[p.name as string].toLocaleString() }}</span>
          <span
            v-else-if="(p as any).works_estimate_required"
            class="costs-value costs-value--readonly costs-value--required"
          >£? — required</span>
          <span
            v-else
            class="costs-value costs-value--readonly"
          >£?</span>
        </div>
      </div>
      <ProvenanceToggle v-if="affordability?.total_works?.provenance" :provenance="affordability?.total_works?.provenance" title="Cost of works" />

      <!-- Rental Income (editable by current person) -->
      <div class="costs-row">
        <span class="costs-label">Rental Income</span>
        <div v-if="editingRental" class="costs-edit-group">
          <span class="costs-edit-prefix">£</span>
          <input
            v-model="editRentalValue"
            type="number"
            class="costs-edit-input"
            autofocus
            @keydown="handleRentalKeydown"
            @blur="saveRental"
          />
        </div>
        <span
          v-else
          class="costs-value"
          :class="{ 'costs-value--editable': !!currentPerson }"
          @click="currentPerson ? startEditRental() : null"
        >
          <template v-if="affordability?.rental_income?.succeeded && affordability?.rental_income?.value">
            £{{ affordability.rental_income.value.amount }}
          </template>
          <template v-else>£0</template>
        </span>
              </div>
      <ProvenanceToggle v-if="affordability?.rental_income?.provenance" :provenance="affordability?.rental_income?.provenance" title="Rental income" />

      <!-- Monthly cost by group — S+L and the other adults are shown as
           SEPARATE blocks; the per-group components come from the DAG
           node so the split is never recomputed here. -->
      <template v-if="affordability?.group_monthly_cost?.succeeded && affordability?.group_monthly_cost?.value?.couple">
        <div class="costs-row costs-row--group">
          <span class="costs-label">
            {{ affordability.group_monthly_cost.value.couple_label }}
            <ProvenanceToggle v-if="affordability?.group_monthly_cost?.provenance" :provenance="affordability?.group_monthly_cost?.provenance" title="Total monthly cost" />
          </span>
          <span class="costs-value" :title="totalMonthlyApprox ? 'Council tax estimated — total is approximate' : undefined">
            {{ totalMonthlyApprox ? '≈ ' : '' }}£{{ affordability.group_monthly_cost.value.couple.value }}/mo
          </span>
        </div>
        <div v-if="affordability.group_monthly_cost.value.couple_breakdown" class="costs-group-breakdown">
          <div v-for="(row, key) in coupleRows()" :key="key" class="costs-row costs-row--sub">
            <span class="costs-label">{{ row.label }}</span>
            <span class="costs-value">{{ row.value }}</span>
            <ProvenanceToggle
              v-if="row.provenance"
              :provenance="row.provenance"
              :title="row.label"
            />
          </div>
        </div>
        <div v-if="affordability?.group_monthly_cost?.value?.others" class="costs-row costs-row--group costs-row--group-others">
          <span class="costs-label">
            {{ affordability.group_monthly_cost.value.others_label }}
            <ProvenanceToggle v-if="affordability?.group_monthly_cost?.provenance" :provenance="affordability?.group_monthly_cost?.provenance" title="Total monthly cost" />
          </span>
          <span class="costs-value">
            {{ totalMonthlyApprox ? '≈ ' : '' }}£{{ affordability.group_monthly_cost.value.others.value }}/mo
          </span>
        </div>
        <div v-if="affordability.group_monthly_cost.value.others_breakdown" class="costs-group-breakdown">
          <div v-for="(row, key) in othersRows()" :key="key" class="costs-row costs-row--sub">
            <span class="costs-label">{{ row.label }}</span>
            <span class="costs-value">{{ row.value }}</span>
            <ProvenanceToggle
              v-if="row.provenance"
              :provenance="row.provenance"
              :title="row.label"
            />
          </div>
        </div>
      </template>
      <template v-else>
        <div class="costs-row costs-row--total" :class="{ 'costs-row--impossible': isImpossible(affordability?.group_monthly_cost) }">
          <span class="costs-label">Total Monthly</span>
          <span v-if="isImpossible(affordability?.group_monthly_cost)" class="costs-value costs-value--impossible">Can't calculate</span>
          <span v-else class="costs-value">?</span>
                </div>
      </template>
      <p v-if="totalBlockedReason" class="costs-note costs-note--blocked">{{ totalBlockedReason }}</p>
      <p v-if="!affordability?.council_tax?.succeeded" class="costs-note">
        Couldn't look up Council Tax — make sure the property's address is complete and correct
        (Edit address above).
      </p>
      <ProvenanceToggle v-if="affordability?.group_monthly_cost?.provenance" :provenance="affordability?.group_monthly_cost?.provenance" title="Total monthly housing cost" />
    </div>

    <!-- EPC scale -->
    <div v-if="epc?.succeeded" class="epc-section">
      <h3 class="epc-title">EPC Rating</h3>
      <div class="epc-scale">
        <div v-for="band in ['A','B','C','D','E','F','G']" :key="band"
          class="epc-step" :class="epcStepClass(band)">
          {{ band }}
          <span v-if="(epc.value?.band ?? '').toUpperCase() === band" class="epc-step__marker">▲</span>
        </div>
      </div>
      <div v-if="epc.value?.potential" class="epc-potential">
        Potential: {{ epc.value.potential }}
      </div>
    </div>
  </section>
</template>

<style scoped>
.detail-section {
  padding: var(--sp-5) var(--sp-6);
  border-bottom: 8px solid var(--slate-50);
}
.detail-section__title {
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  color: var(--slate-800);
  margin: 0 0 var(--sp-4);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.costs-table { display: flex; flex-direction: column; }
.costs-row {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  padding: var(--sp-2) 0;
  border-bottom: 1px solid var(--border);
}
/* The provenance body must break out of the horizontal row onto its own
   full-width line — when it participates in the label/value flex line,
   mobile squeezes the label, shoves the £ value off-screen, and the row
   grows to ~1,000px. display:contents keeps the closed ⓘ exactly where
   it was; only the opened body wraps. */
.costs-row > .provenance-toggle { display: contents; }
.costs-row > .provenance-toggle .provenance-toggle__body { flex-basis: 100%; min-width: 0; }
.costs-row--sub { padding-left: var(--sp-5); border-bottom: none; }
.costs-row--total { font-weight: var(--fw-bold); border-bottom: none; border-top: 2px solid var(--slate-200); margin-top: var(--sp-2); padding-top: var(--sp-3); }
.costs-row--group { font-weight: var(--fw-bold); border-bottom: none; border-top: 2px solid var(--slate-200); margin-top: var(--sp-3); padding-top: var(--sp-3); }
.costs-row--group-others { margin-top: var(--sp-4); }
.costs-row--group .costs-label {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
}
.costs-row--group .costs-label .provenance-toggle__trigger {
  font-size: 0.9rem;
  color: var(--text-muted);
}
/* Same breakout for the group rows, where the toggle sits inside the
   label: the opened body takes a full-width second line of the label. */
.costs-row--group .costs-label > .provenance-toggle { display: contents; }
.costs-row--group .costs-label .provenance-toggle__body { flex-basis: 100%; min-width: 0; }
.costs-group-breakdown { margin-top: var(--sp-1); }
.costs-row--impossible { opacity: 0.5; }
.costs-label { font-size: var(--fs-sm); color: var(--text); }
.costs-value { font-size: var(--fs-sm); font-weight: var(--fw-semibold); margin-left: auto; margin-right: var(--sp-2); }
.costs-value--impossible { color: var(--red); font-style: italic; }
.costs-value--readonly { color: var(--text-secondary); }
.costs-value--required { color: var(--red); font-style: italic; }
.costs-value--editable {
  color: var(--blue);
  cursor: pointer;
  border-bottom: 1px dashed var(--blue);
  padding-bottom: 1px;
}
.costs-value--editable:hover {
  background: var(--slate-50);
  border-radius: 2px;
}
.costs-subsection { display: flex; flex-direction: column; }
.costs-note {
  color: var(--text-muted);
  font-size: 0.85rem;
  margin: 0.25rem 0 0.75rem;
}
.costs-note--blocked {
  color: var(--red);
}
.costs-provenance { margin: var(--sp-2) 0; }
.costs-edit-group { display: flex; align-items: center; gap: 2px; margin-left: auto; margin-right: var(--sp-2); }
.costs-edit-prefix { font-size: var(--fs-sm); font-weight: var(--fw-semibold); color: var(--text); }
.costs-edit-input {
  width: 100px;
  padding: 2px 6px;
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  border: 1px solid var(--blue);
  border-radius: var(--radius);
  outline: none;
  text-align: right;
}


/* EPC */
.epc-section { margin-top: var(--sp-6); }
.epc-title { font-size: var(--fs-sm); font-weight: var(--fw-bold); margin: 0 0 var(--sp-3); color: var(--slate-700); }
.epc-scale { display: flex; gap: 2px; }
.epc-step {
  flex: 1;
  text-align: center;
  padding: var(--sp-2) 0;
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  border-radius: 4px;
  color: var(--slate-500);
  background: var(--slate-100);
  position: relative;
}
.epc-step__marker { position: absolute; bottom: -6px; left: 50%; transform: translateX(-50%); font-size: var(--fs-sm); }
.epc-step--a { background: var(--epc-a); color: #fff; }
.epc-step--b { background: var(--epc-b); color: #fff; }
.epc-step--c { background: var(--epc-c); color: #fff; }
.epc-step--d { background: var(--epc-d); color: #1a1a1a; }
.epc-step--e { background: var(--epc-e); color: #fff; }
.epc-step--f { background: var(--epc-f); color: #fff; }
.epc-step--g { background: var(--epc-g); color: #fff; }
.epc-potential { font-size: var(--fs-sm); color: var(--text-secondary); margin-top: var(--sp-5); }
</style>
