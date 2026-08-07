<script setup lang="ts">
import { RouterView } from 'vue-router'
import { onMounted } from 'vue'
import { useWebSocket } from './composables/useWebSocket'
import { useAuthStore } from './stores/auth'

const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
const wsHost = window.location.host
useWebSocket().connect(`${wsProtocol}//${wsHost}/api/ws`)

onMounted(() => {
  useAuthStore().checkAuth()
})
</script>

<template>
  <RouterView />
</template>

<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font-size: 16px; -webkit-text-size-adjust: 100%; }
body {
  font-family: var(--font);
  color: var(--text);
  background: var(--page-bg);
  line-height: var(--lh);
  min-height: 100vh;
}
button { font: inherit; cursor: pointer; border: none; background: none; }

/* Shared tab bar — the settings page AND the what-if panel (layout parity) */
.settings-tabs {
  display: flex;
  gap: var(--sp-2);
  padding: var(--sp-2) 0;
  position: sticky;
  top: var(--header-h);
  background: var(--page-bg);
  z-index: 5;
}
.settings-tabs button {
  flex: 1;
  border: 1px solid var(--border);
  background: var(--card-bg);
  color: var(--text-secondary);
  border-radius: var(--radius);
  font-size: var(--fs-base);
  font-weight: var(--fw-semibold);
  min-height: 44px;
  cursor: pointer;
}
.settings-tabs button.settings-tab--active {
  background: var(--green);
  border-color: var(--green);
  color: #fff;
}
.settings-panel { display: flex; flex-direction: column; gap: var(--sp-4); }


[hidden] { display: none !important; }

:root {
  /* ── Warm neutral palette (redesign mockup) ── */
  --slate-50: #f5f3ef;
  --slate-100: #f0ece6;
  --slate-200: #e5e1db;
  --slate-300: #d6d0c8;
  --slate-400: #b3aca3;
  --slate-500: #9e9891;
  --slate-600: #6b6560;
  --slate-700: #4a4541;
  --slate-800: #2b2825;
  --slate-900: #1a1a1a;

  /* ── Semantic colors ── */
  --green: #2d6a4f;
  --green-bg: #d8f3dc;
  --green-text: #1f4d38;
  --orange: #e09f3e;
  --orange-bg: #fef3e2;
  --orange-text: #7f4f24;
  --red: #c1121f;
  --red-bg: #fde8e8;
  --red-text: #9a0e18;
  --blue: #2d6a4f;
  --blue-bg: #d8f3dc;
  --blue-text: #1f4d38;
  --amber: #e09f3e;
  --amber-bg: #fef3e2;
  --amber-text: #7f4f24;
  --purple: #8b5cf6;
  --purple-bg: #ede9fe;
  --purple-text: #6d28d9;
  --commute-none: #adb5bd;
  --epc-a: #2e7d32;
  --epc-b: #4caf50;
  --epc-c: #8bc34a;
  --epc-d: #ffeb3b;
  --epc-e: #ff9800;
  --epc-f: #e65100;
  --epc-g: #c62828;

  /* ── Neutrals ── */
  --text: #1a1a1a;
  --text-secondary: #6b6560;
  --text-muted: #9e9891;
  --border: #e5e1db;
  --divider: #eee9e2;
  --card-bg: #ffffff;
  --page-bg: #f5f3ef;
  --header-bg: #ffffff;
  --pill-bg: #f0ece6;

  /* ── Typography ── */
  --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --font-mono: "JetBrains Mono", "SF Mono", "Cascadia Code", monospace;
  --fs-2xs: 0.625rem;
  --fs-xs: 0.6875rem;
  --fs-sm: 0.75rem;
  --fs-md: 0.8125rem;
  --fs-base: 0.875rem;
  --fs-lg: 0.9375rem;
  --fs-xl: 1.125rem;
  --fs-2xl: 1.5rem;
  --lh-tight: 1.25;
  --lh: 1.5;
  --lh-loose: 1.75;
  --fw-normal: 400;
  --fw-medium: 500;
  --fw-semibold: 600;
  --fw-bold: 700;

  /* ── Spacing (4px base) ── */
  --sp-1: 4px;
  --sp-2: 8px;
  --sp-3: 12px;
  --sp-4: 16px;
  --sp-5: 20px;
  --sp-6: 24px;
  --sp-8: 32px;
  --sp-10: 40px;
  --sp-12: 48px;

  /* ── Radii ── */
  --radius-sm: 8px;
  --radius: 12px;
  --radius-lg: 16px;
  --radius-xl: 20px;
  --radius-full: 100px;

  /* ── Shadows ── */
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.06);
  --shadow: 0 2px 8px rgba(0,0,0,0.08);
  --shadow-md: 0 4px 16px rgba(0,0,0,0.10);
  --shadow-lg: 0 8px 24px rgba(0,0,0,0.12);

  /* ── Transitions ── */
  --transition: 180ms ease-in-out;

  /* ── Layout ── */
  --header-h: 52px;
  --tabbar-h: 56px;
  --content-max-w: 960px;
}
</style>
