<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { fetchComments, postComment, type CommentEntry } from '../services/api'

const props = defineProps<{
  rid: string
}>()

const comments = ref<CommentEntry[]>([])
const newComment = ref('')
const submitting = ref(false)
const commentError = ref<string | null>(null)

function relativeTime(iso: string): string {
  const d = new Date(iso)
  const now = Date.now()
  const diffMs = now - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  const diffDay = Math.floor(diffHr / 24)
  if (diffDay < 7) return `${diffDay}d ago`
  return d.toLocaleDateString()
}

async function loadComments() {
  try {
    comments.value = await fetchComments(props.rid)
  } catch {
    comments.value = []
  }
}

async function submitComment() {
  const text = newComment.value.trim()
  if (!text || submitting.value) return
  submitting.value = true
  commentError.value = null
  try {
    const entry = await postComment({ rid: props.rid, text })
    comments.value.push(entry)
    newComment.value = ''
  } catch {
    commentError.value = 'Failed to save comment. Please try again.'
  } finally {
    submitting.value = false
  }
}

onMounted(loadComments)
</script>

<template>
  <section id="section-notes" class="detail-section">
    <h2 class="detail-section__title">Notes</h2>

    <div v-if="comments.length === 0" class="notes-empty">
      No comments yet.
    </div>
    <div v-else class="notes-list">
      <div v-for="(comment, i) in comments" :key="i" class="note-card">
        <div class="note-card__header">
          <div class="note-card__author">
            <span class="note-card__avatar">{{ comment.person[0] }}</span>
            <span class="note-card__name">{{ comment.person }}</span>
          </div>
          <span class="note-card__time">{{ relativeTime(comment.timestamp) }}</span>
        </div>
        <div class="note-card__body">{{ comment.text }}</div>
      </div>
    </div>

    <!-- Add comment form -->
    <div class="note-input">
      <textarea
        v-model="newComment"
        class="note-input__textarea"
        placeholder="Add a note…"
        rows="2"
      ></textarea>
      <div class="note-input__actions">
        <button
          class="note-input__btn"
          :disabled="!newComment.trim() || submitting"
          @click="submitComment"
        >
          {{ submitting ? 'Saving…' : 'Save' }}
        </button>
      </div>
      <p v-if="commentError" class="note-input__error">{{ commentError }}</p>
    </div>
  </section>
</template>

<style scoped>
.detail-section {
  background: var(--card-bg, #fff);
  border-radius: var(--r-card, 12px);
  padding: var(--sp-16, 16px);
}

.detail-section__title {
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--slate-500, #64748b);
  margin-bottom: 12px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--slate-200, #e2e8f0);
}

.notes-empty {
  padding: 24px 16px;
  text-align: center;
  color: var(--slate-400, #94a3b8);
  font-style: italic;
}

.notes-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.note-card {
  background: var(--slate-50, #f8fafc);
  border-radius: 8px;
  padding: 10px 14px;
}

.note-card__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.note-card__author {
  display: flex;
  align-items: center;
  gap: 6px;
}

.note-card__avatar {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--blue, #1a73e8);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
}

.note-card__name {
  font-size: 13px;
  font-weight: 600;
}

.note-card__time {
  font-size: 11px;
  color: var(--slate-400, #94a3b8);
}

.note-card__body {
  font-size: 13px;
  line-height: 1.4;
  color: var(--text);
  white-space: pre-wrap;
}

.note-input {
  margin-top: 12px;
}

.note-input__textarea {
  width: 100%;
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid var(--slate-200, #e2e8f0);
  background: var(--card-bg, #fff);
  color: var(--text);
  resize: vertical;
  font-family: inherit;
  font-size: 13px;
  box-sizing: border-box;
}

.note-input__actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 6px;
}

.note-input__btn {
  padding: 6px 16px;
  border-radius: 6px;
  border: none;
  background: var(--blue, #1a73e8);
  color: #fff;
  font-weight: 600;
  font-size: 13px;
  cursor: pointer;
}

.note-input__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.note-input__error {
  margin-top: 8px;
  font-size: 13px;
  color: var(--red, #dc2626);
}
</style>
