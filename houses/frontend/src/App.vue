<script setup lang="ts">
import { RouterView } from 'vue-router'
import { useWebSocket } from './composables/useWebSocket'

const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
const wsHost = window.location.host
useWebSocket().connect(`${wsProtocol}//${wsHost}/api/ws`)
</script>

<template>
  <RouterView />
</template>

<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font-size: 16px; -webkit-text-size-adjust: 100%; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: #1a1a1a;
  background: #f4f5f7;
  line-height: 1.5;
  min-height: 100vh;
}
button { font: inherit; cursor: pointer; border: none; background: none; }
[hidden] { display: none !important; }

:root {
  --green: #2e7d32;
  --green-bg: #e8f5e9;
  --orange: #e65100;
  --orange-bg: #fff3e0;
  --red: #c62828;
  --red-bg: #ffebee;
  --blue: #1565c0;
  --blue-bg: #e3f2fd;
  --muted: #9e9e9e;
  --muted-bg: #f0f0f0;
  --text: #1a1a1a;
  --text-secondary: #666;
  --text-muted: #999;
  --border: #e0e0e0;
  --divider: #eee;
  --card-bg: #fff;
  --page-bg: #f4f5f7;
  --header-bg: #1a2a3a;
  --radius: 12px;
  --radius-sm: 8px;
  --radius-pill: 999px;
  --shadow: 0 1px 4px rgba(0,0,0,0.06);
}
</style>
