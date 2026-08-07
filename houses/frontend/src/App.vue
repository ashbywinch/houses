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

/* ═══════ Settings/What-if shared layout (from the tabbed prototype) ═══════ */
.settings-card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: var(--sp-3) var(--sp-4);
  margin-bottom: var(--sp-2);
}
.card-heading {
  font-size: var(--fs-sm);
  font-weight: var(--fw-bold);
  color: var(--text);
  margin: 0 0 var(--sp-3);
}
.divider {
  border: none;
  border-top: 1px solid var(--divider);
  margin: var(--sp-3) 0;
}
.stack-field {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: var(--sp-2);
}
.stack-field > label { font-size: var(--fs-sm); font-weight: var(--fw-semibold); color: var(--text-secondary); }
.stack-field > input {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-base);
  font-family: inherit;
  color: var(--text);
  background: var(--card-bg);
  min-height: 44px;
  box-sizing: border-box;
}

/* Toggle row + switch */
.toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 48px;
  padding: var(--sp-2) var(--sp-4);
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  margin-bottom: var(--sp-2);
  cursor: pointer;
}
.toggle-row__label { font-size: var(--fs-base); font-weight: var(--fw-semibold); color: var(--text); }
.toggle-row__hint { font-size: var(--fs-xs); color: var(--text-muted); margin-top: 1px; }
.switch {
  position: relative;
  width: 52px;
  height: 32px;
  flex-shrink: 0;
  background: none;
  border: none;
  padding: 0;
}
.switch__track {
  position: absolute;
  inset: 0;
  background: var(--slate-200);
  border-radius: 16px;
  transition: background 0.2s;
}
.switch__knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 28px;
  height: 28px;
  background: #fff;
  border-radius: 50%;
  box-shadow: 0 1px 3px rgba(0,0,0,0.2);
  transition: transform 0.2s;
  pointer-events: none;
}
.switch--on .switch__track { background: var(--green); }
.switch--on .switch__knob { transform: translateX(20px); }

/* One destination = one card with a green left accent */
.dest-card { border-left: 4px solid var(--green); }
.dest-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  margin-bottom: var(--sp-2);
}
.dest-card__name { font-size: var(--fs-base); font-weight: var(--fw-bold); color: var(--text); }
.dest-card__meta { font-size: var(--fs-xs); color: var(--text-muted); }

/* Mode pills */
.mode-pills { display: flex; gap: var(--sp-2); flex-wrap: wrap; }
.mode-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border);
  border-radius: var(--radius-full);
  padding: var(--sp-2) var(--sp-4);
  font-size: var(--fs-sm);
  font-weight: var(--fw-medium);
  color: var(--text-secondary);
  background: var(--card-bg);
  min-height: 44px;
  cursor: pointer;
  font-family: inherit;
}
.mode-pill--active {
  background: var(--green-bg);
  border-color: var(--green);
  color: var(--green-text);
  font-weight: var(--fw-semibold);
}
.mode-pill:disabled { opacity: 0.5; cursor: not-allowed; }
.mode-pill--hidden { display: none; }

/* Address field with lookup */
.address-group { position: relative; }
.address-input {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: var(--sp-2) var(--sp-3);
  padding-right: 48px;
  font-size: var(--fs-base);
  font-family: inherit;
  color: var(--text);
  background: var(--card-bg);
  min-height: 44px;
  box-sizing: border-box;
}
.address-input::placeholder { color: var(--text-muted); font-style: italic; }
.address-input:focus { outline: none; border-color: var(--green); box-shadow: 0 0 0 2px var(--green-bg); }
.address-lookup {
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  width: 40px;
  height: 40px;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--green-bg);
  color: var(--green-text);
  font-size: 1.1rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.address-lookup:hover { background: var(--green); color: #fff; }

/* Commute colour bands */
.band-row {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  margin: var(--sp-2) 0;
}
.band {
  flex: 1;
  display: flex;
  height: 10px;
  border-radius: var(--radius-full);
  overflow: hidden;
  min-width: 0;
}
.band__good { flex: 1; background: var(--green); }
.band__warn { flex: 1; background: var(--orange); }
.band__bad  { flex: 1; background: var(--red); }
.input-half {
  min-width: 48px;
  max-width: 48px;
  text-align: center;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: var(--sp-2) 2px;
  font-size: var(--fs-sm);
  font-family: inherit;
  font-weight: var(--fw-semibold);
  color: var(--text);
  background: var(--card-bg);
  min-height: 40px;
  flex-shrink: 0;
}
.band-caption {
  display: flex;
  justify-content: space-between;
  font-size: var(--fs-2xs);
  color: var(--text-muted);
}
.band-preview {
  display: flex;
  gap: var(--sp-3);
  margin-top: var(--sp-3);
  flex-wrap: wrap;
}
.band-preview__item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-xs);
  color: var(--text-secondary);
}
.band-preview__pill {
  border-radius: var(--radius-full);
  font-size: var(--fs-2xs);
  font-weight: var(--fw-bold);
  white-space: nowrap;
  padding: 2px 8px;
  line-height: 1.6;
  display: inline-flex;
}
.band-preview__pill--good { background: var(--green); color: #fff; }
.band-preview__pill--warn { background: var(--orange); color: #fff; }
.band-preview__pill--bad  { background: var(--red); color: #fff; }
.band-helper {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  line-height: var(--lh);
  margin: var(--sp-2) 0 0;
}

/* Deposit hero */
.deposit-hero { margin-bottom: var(--sp-2); }
.deposit-hero__label {
  font-size: var(--fs-2xs);
  font-weight: var(--fw-semibold);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 2px;
  display: block;
}
.deposit-hero__number {
  font-size: 2rem;
  font-weight: var(--fw-bold);
  color: var(--green);
  letter-spacing: -0.03em;
  display: block;
  line-height: 1.1;
}
.deposit-rows {
  margin: 0;
  padding: 0;
  list-style: none;
}
.deposit-rows li {
  display: flex;
  justify-content: space-between;
  padding: var(--sp-1) 0;
  border-bottom: 1px solid var(--divider);
  font-size: var(--fs-sm);
}
.deposit-rows li .amount { font-weight: var(--fw-semibold); color: var(--text); }
.deposit-rows__zero { opacity: 0.45; }

/* Buttons + footer */
.btn-add {
  width: 100%;
  border: none;
  border-radius: var(--radius);
  background: var(--green);
  color: #fff;
  font-size: var(--fs-base);
  font-weight: var(--fw-semibold);
  min-height: 48px;
  cursor: pointer;
  margin: var(--sp-2) 0 var(--sp-4);
  font-family: inherit;
}
.info-link {
  color: var(--text-secondary);
  font-size: var(--fs-xs);
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;
  background: none;
  border: none;
  padding: var(--sp-1) 0;
  font-family: inherit;
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
}
.save-footer {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  background: var(--card-bg);
  border-top: 1px solid var(--border);
  padding: var(--sp-2) var(--sp-4);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  justify-content: center;
  z-index: 30;
}
.save-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--green);
}


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
