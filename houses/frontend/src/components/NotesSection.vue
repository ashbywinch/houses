<script setup lang="ts">
import { ref, watch } from 'vue'
import { usePropertiesStore } from '../stores/properties'
import { patchTriage } from '../services/api'

const props = defineProps<{
  rid: string
  triage: any
  comments: any
}>()

const store = usePropertiesStore()

// ── Notes state ────────────────────────────────────────
const userNotes = ref('')
const notesSaved = ref(false)
const triageStatus = ref('')

// Initialize notes from existing triage data when detail loads
watch([() => props.triage, () => props.comments], () => {
  const t = props.triage
  if (t?.user_notes) userNotes.value = t.user_notes as string
  if (t?.triage_status) triageStatus.value = t.triage_status as string
}, { immediate: true })

async function saveNotes() {
  notesSaved.value = false
  await patchTriage(props.rid, { user_notes: userNotes.value })
  // Update local store state so it persists across page views
  if (!store.triage[props.rid]) {
    store.triage[props.rid] = { favourite: false, dismissed: false, is_viewed: false, user_notes: '', triage_status: '' }
  }
  store.triage[props.rid].user_notes = userNotes.value
  notesSaved.value = true
  setTimeout(() => { notesSaved.value = false }, 2000)
}

async function setStatus(status: string) {
  triageStatus.value = status
  await patchTriage(props.rid, { triage_status: status })
  if (store.triage[props.rid]) {
    store.triage[props.rid].triage_status = status
  }
}

async function markViewed() {
  await store.toggleTriage(props.rid, 'is_viewed', true)
}
</script>

<template>
  <section id="section-notes" class="detail-section">
    <h2 class="detail-section__title">Notes</h2>

    <!-- Status dropdown -->
    <div class="detail-field">
      <span class="detail-field__label">Status</span>
      <div class="detail-field__value">
        <select v-model="triageStatus" class="notes-select" @change="setStatus(triageStatus)">
          <option value="">None</option>
          <option value="shortlisted">Shortlisted</option>
          <option value="offer_made">Offer Made</option>
          <option value="rejected">Rejected</option>
        </select>
      </div>
    </div>

    <!-- Free-text notes -->
    <div class="detail-field detail-field--block">
      <span class="detail-field__label">Personal Notes</span>
      <textarea v-model="userNotes" class="notes-textarea" placeholder="Add your notes about this property..." rows="4" />
      <div class="notes-actions">
        <button class="btn--small" @click="saveNotes">Save Notes</button>
        <span v-if="notesSaved" class="notes-saved">Saved!</span>
      </div>
    </div>

    <!-- Mark as Viewed -->
    <div class="detail-field">
      <button class="btn--small btn--confirm" @click="markViewed">
        {{ triage?.is_viewed ? '✓ Viewed' : 'Mark as Viewed' }}
      </button>
    </div>

    <!-- Group notes (read-only) -->
    <div v-if="comments?.group_notes?.value" class="detail-field detail-field--block">
      <span class="detail-field__label">Group Notes</span>
      <p class="notes-readonly">{{ comments.group_notes.value }}</p>
    </div>
    <div v-if="comments?.ashby_comments?.value" class="detail-field detail-field--block">
      <span class="detail-field__label">Ashby's Notes</span>
      <p class="notes-readonly">{{ comments.ashby_comments.value }}</p>
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
.detail-field--block { flex-direction: column; align-items: stretch; }
.detail-field__label { font-size: 13px; font-weight: 600; color: var(--text-secondary); min-width: 80px; }
.detail-field__value { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 14px; }
.notes-select {
  font: inherit; padding: 8px 12px; border: 1px solid var(--border);
  border-radius: 8px; font-size: 14px; min-width: 160px; min-height: 44px;
}
.notes-textarea {
  font: inherit; padding: 8px 12px; border: 1px solid var(--border);
  border-radius: 8px; font-size: 14px; width: 100%; resize: vertical;
}
.notes-actions { display: flex; align-items: center; gap: 8px; }
.notes-saved { font-size: 13px; color: var(--green); font-weight: 600; }
.btn--small {
  font-size: 12px; padding: 8px 16px; border: 1px solid var(--border);
  border-radius: 8px; background: var(--card-bg); cursor: pointer;
  min-width: 44px; min-height: 44px;
}
.btn--confirm { background: var(--green-bg); color: var(--green); border-color: var(--green); }
.notes-readonly { font-size: 14px; color: var(--text-secondary); margin: 0; white-space: pre-wrap; }
</style>
