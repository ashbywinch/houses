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
const settingsOpen = ref(false)

function toggleSettingsMenu() {
  settingsOpen.value = !settingsOpen.value
}

function closeSettingsMenu() {
  settingsOpen.value = false
}

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
          <div class="header__menu">
            <button
              class="header__settings-menu"
              :aria-expanded="settingsOpen"
              @click="toggleSettingsMenu"
            >Settings ▾</button>
            <div v-if="settingsOpen" class="header__menu-list">
              <router-link class="header__menu-item" to="/settings" @click="closeSettingsMenu">
                Family settings
              </router-link>
              <router-link
                v-for="p in persons"
                :key="p.name"
                class="header__menu-item header__menu-item--person"
                :to="'/settings?person=' + encodeURIComponent(p.name)"
                @click="closeSettingsMenu"
              >{{ p.name }}</router-link>
            </div>
          </div>
          <button
            v-if="auth.user.is_superuser"
            class="header__su-toggle"
            :class="{ 'header__su-toggle--active': auth.superuserMode }"
            @click="auth.toggleSuperuser()"
            title="Admin: switch between your view and acting as someone else"
          >
            Admin
          </button>
          <button class="header__auth-btn" @click="auth.logout()">Logout</button>
        </template>
        <template v-else>
          <button class="header__auth-btn" @click="auth.login()">Login</button>
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
}
.header__inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  max-width: 1200px;
  margin: 0 auto;
}
.header__title {
  font-size: 1.125rem;
  font-weight: var(--fw-bold);
  letter-spacing: -0.01em;
  color: var(--text);
}
.header__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.header__auth-btn,
.header__su-toggle,
.header__settings-menu {
  background: none;
  border: none;
  color: var(--text-secondary);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  font-size: 0.8125rem;
  font-weight: var(--fw-medium);
  cursor: pointer;
  transition: background 0.15s;
}
.header__auth-btn:hover,
.header__su-toggle:hover,
.header__settings-menu:hover {
  background: var(--pill-bg);
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
