<script setup lang="ts">
import { useAuthStore } from '../stores/auth'

defineProps<{ title: string }>()

const auth = useAuthStore()
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
          <button
            v-if="auth.user.is_superuser"
            class="header__su-toggle"
            :class="{ 'header__su-toggle--active': auth.superuserMode }"
            @click="auth.toggleSuperuser()"
            title="Toggle superuser mode"
          >
            SU
          </button>
          <button class="header__auth-btn" @click="auth.logout()">Logout</button>
        </template>
        <template v-else-if="auth.authAvailable">
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
        <!-- Persons injected by parent or fetched from settings -->
        <option v-for="p in ['Ashby', 'Simon', 'Lorena', 'George']" :key="p" :value="p">{{ p }}</option>
      </select>
      <button class="su-bar__exit" @click="auth.toggleSuperuser()">Exit</button>
    </div>
  </header>
</template>

<style scoped>
.header {
  background: var(--header-bg);
  color: #fff;
  position: sticky;
  top: 0;
  z-index: 10;
}
.header__inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  max-width: 1200px;
  margin: 0 auto;
}
.header__title {
  font-size: 20px;
  font-weight: 700;
}
.header__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.header__auth-btn,
.header__su-toggle {
  background: rgba(255, 255, 255, 0.15);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: #fff;
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}
.header__su-toggle--active {
  background: var(--amber-bg, #f59e0b);
  color: #1a1a1a;
  font-weight: 700;
  border-color: var(--amber-bg, #f59e0b);
}
.header__auth-status {
  font-size: 12px;
  opacity: 0.6;
}
/* Superuser bar */
.su-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: var(--amber-bg, #f59e0b);
  color: #1a1a1a;
  font-size: 13px;
  max-width: 1200px;
  margin: 0 auto;
}
.su-bar__label {
  white-space: nowrap;
  font-weight: 600;
}
.su-bar__select {
  padding: 3px 8px;
  border-radius: 4px;
  border: 1px solid rgba(0, 0, 0, 0.2);
  font-size: 13px;
  background: #fff;
  color: #1a1a1a;
}
.su-bar__exit {
  margin-left: auto;
  background: rgba(0, 0, 0, 0.1);
  border: 1px solid rgba(0, 0, 0, 0.2);
  border-radius: 4px;
  padding: 3px 10px;
  font-size: 12px;
  cursor: pointer;
  font-weight: 600;
}
</style>
