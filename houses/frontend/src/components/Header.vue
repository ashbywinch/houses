<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'

defineProps<{ title: string }>()

const auth = useAuthStore()

interface PersonEntry {
  name: string
  email: string
}

const persons = ref<PersonEntry[]>([])

async function fetchPersons() {
  try {
    const r = await fetch('/api/persons')
    if (r.ok) {
      const data = await r.json()
      persons.value = data.persons ?? []
    }
  } catch {
    // Non-critical — dropdown falls back to empty
  }
}

onMounted(fetchPersons)
</script>

<template>
  <header class="header">
    <div class="header__inner">
      <div class="header__actions header__actions--left">
        <slot name="actions" />
      </div>
      <h1 class="header__title">{{ title }}</h1>
      <div class="header__actions header__actions--right">
        <slot name="actions-right" />
        <template v-if="auth.loading">
          <span class="header__auth-status">…</span>
        </template>
        <template v-else-if="auth.user">
          <router-link class="header__settings-link" to="/settings" aria-label="Settings">
            <span class="header__action-icon" aria-hidden="true">⚙</span>
            <span class="header__action-text">Settings</span>
          </router-link>
          <button
            v-if="auth.user.is_superuser"
            class="header__su-toggle"
            :class="{ 'header__su-toggle--active': auth.superuserMode }"
            @click="auth.toggleSuperuser()"
            :title="auth.superuserMode ? 'Stop impersonating' : 'Switch between your view and acting as another family member'"
            :aria-label="auth.superuserMode ? 'Stop impersonating' : 'Switch person'"
          >
            <span class="header__action-icon" aria-hidden="true">👤</span>
            <span class="header__action-text">{{ auth.superuserMode ? 'Stop impersonating' : 'Switch person' }}</span>
          </button>
          <button class="header__auth-btn" @click="auth.logout()" aria-label="Log out">
            <span class="header__action-icon" aria-hidden="true">⎋</span>
            <span class="header__action-text">Logout</span>
          </button>
        </template>
        <template v-else>
          <button class="header__auth-btn" @click="auth.login()" aria-label="Log in">
            <span class="header__action-icon" aria-hidden="true">→</span>
            <span class="header__action-text">Login</span>
          </button>
        </template>
      </div>
    </div>
    <!-- Superuser mode bar -->
    <div v-if="auth.superuserMode && auth.user" class="su-bar">
      <span class="su-bar__label">Superuser mode — acting as:</span>
      <select
        class="su-bar__select"
        :value="auth.impersonating ?? auth.user.person ?? ''"
        @change="(e) => auth.setImpersonating((e.target as HTMLSelectElement).value)"
      >
        <option value="" disabled>Select a person…</option>
        <option v-for="p in persons" :key="p.name" :value="p.name">{{ p.name }}</option>
      </select>
      <button class="su-bar__exit" @click="auth.toggleSuperuser()">Exit</button>
    </div>
  </header>
</template>

<style scoped>
.header {
  background: var(--header-bg);
  color: var(--text);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 10;
  overflow-x: hidden;
}
.header__inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  max-width: 1200px;
  margin: 0 auto;
  gap: 8px;
}
.header__title {
  font-size: 1.125rem;
  font-weight: var(--fw-bold);
  letter-spacing: -0.01em;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 0 0 auto;
  min-width: 0;
}
/* Mobile header pattern: ONE row. The title truncates and the text
   actions collapse to icon-only (label stays in the accessible
   name/tooltip), so nothing wraps and every action stays reachable. */
@media (max-width: 560px) {
  .header__inner {
    padding: 8px 10px;
    gap: 4px;
  }
  .header__title {
    font-size: 1rem;
  }
  .header__action-text {
    display: none;
  }
  .header__auth-btn,
  .header__su-toggle,
  .header__settings-link {
    padding: 6px 4px;
    font-size: 1rem;
    line-height: 1;
  }
  .header__action-icon {
    font-size: 1.15rem;
  }
  .header__actions {
    gap: 2px;
  }
}
.header__actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
.header__auth-btn,
.header__su-toggle,
.header__settings-link {
  background: none;
  border: none;
  color: var(--text-secondary);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  font-size: 0.8125rem;
  font-weight: var(--fw-medium);
  cursor: pointer;
  transition: background 0.15s;
  text-decoration: none;
  white-space: nowrap;
}
.header__action-icon {
  display: inline-block;
}
.header__settings-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.header__auth-btn,
.header__su-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.header__su-toggle {
  border: 1px solid var(--amber-text);
  color: var(--amber-text);
}
.header__su-toggle:hover {
  background: var(--amber-bg);
}
.header__su-toggle--active {
  background: var(--amber-bg);
  color: var(--amber-text);
  font-weight: var(--fw-bold);
}
.header__auth-status {
  font-size: var(--fs-sm);
  opacity: 0.6;
}
/* Superuser bar */
.su-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: var(--amber-bg);
  color: var(--amber-text);
  font-size: var(--fs-sm);
  max-width: 1200px;
  margin: 0 auto;
}
.su-bar__label {
  white-space: nowrap;
  font-weight: var(--fw-semibold);
}
.su-bar__select {
  padding: 3px 8px;
  border-radius: 4px;
  border: 1px solid var(--border);
  font-size: var(--fs-sm);
  background: #fff;
  color: var(--text);
}
.su-bar__exit {
  margin-left: auto;
  background: rgba(0, 0, 0, 0.1);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 3px 10px;
  font-size: var(--fs-sm);
  cursor: pointer;
  font-weight: var(--fw-semibold);
}
</style>
