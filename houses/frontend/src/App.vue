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
[hidden] { display: none !important; }

:root {
  /* ── Slate palette ── */
  --slate-50: #f8fafc;
  --slate-100: #f1f5f9;
  --slate-200: #e2e8f0;
  --slate-300: #cbd5e1;
  --slate-400: #94a3b8;
  --slate-500: #64748b;
  --slate-600: #475569;
  --slate-700: #334155;
  --slate-800: #1e293b;
  --slate-900: #0f172a;

  /* ── Semantic colors ── */
  --green: #16a34a;
  --green-bg: #dcfce7;
  --green-text: #15803d;
  --orange: #f97316;
  --epc-a: #2e7d32;
  --epc-b: #4caf50;
  --epc-c: #8bc34a;
  --epc-d: #ffeb3b;
  --epc-e: #ff9800;
  --epc-f: #e65100;
  --epc-g: #c62828;
  --orange-bg: #ffedd5;
  --orange-text: #ea580c;
  --red: #dc2626;
  --red-bg: #fee2e2;
  --red-text: #b91c1c;
  --blue: #3b82f6;
  --blue-bg: #dbeafe;
  --blue-text: #2563eb;
  --amber: #f59e0b;
  --amber-bg: #fef3c7;
  --amber-text: #92400e;
  --purple: #8b5cf6;
  --purple-bg: #ede9fe;
  --purple-text: #6d28d9;

  /* ── Neutrals ── */
  --text: #0f172a;
  --text-secondary: #475569;
  --text-muted: #94a3b8;
  --border: #e2e8f0;
  --divider: #f1f5f9;
  --card-bg: #fff;
  --page-bg: #f8fafc;
  --header-bg: #1e293b;

  /* ── Typography ── */
  --font: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", "SF Mono", "Cascadia Code", monospace;
  --fs-xs: 0.6875rem;
  --fs-sm: 0.8125rem;
  --fs-base: 0.9375rem;
  --fs-lg: 1.0625rem;
  --fs-xl: 1.25rem;
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
  --radius-sm: 4px;
  --radius: 8px;
  --radius-lg: 12px;
  --radius-xl: 16px;
  --radius-full: 999px;

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
