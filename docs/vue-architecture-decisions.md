# Vue Architecture Decisions

Recorded decisions for the Vue 3 SPA replacing Jinja2 templates (Phase 4), with the reasoning. Component/file structure is discoverable in the repo — this page records the **choices and why**, so a future change doesn't silently reverse them.

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Composition API with `<script setup lang="ts">`** for all components | Recommended Vue 3 SFC syntax — less boilerplate, better TS inference, compile-time optimizations |
| 2 | **Pinia** for global state | Property data, commute results, WebSocket updates shared across components/views; DevTools + typed stores + explicit mutations; no SSR concerns (browser-only SPA) |
| 3 | **Vue Router, hash mode** | Routes `/` (list) and `/property/:rid` (detail). Hash mode avoids backend route config — backend serves the SPA at root |
| 4 | **Vite proxies `/api` → `http://localhost:8080`** | No CORS config needed on the backend |
| 5 | **Native WebSocket composable** (`useWebSocket`) | Connects on mount, receives property updates, updates the Pinia store — no external library needed |
| 6 | **Native `fetch()` in `services/api.ts`** | No axios; thin wrapper is sufficient for the REST calls |
| 7 | **Vitest + @vue/test-utils** | Unit-test composables/stores in isolation; component tests `mount()`/`shallowMount()` with mocked API responses; no e2e for the MVP |
| 8 | **Scoped styles per `.vue` file** | Replicate the exact CSS from `docs/current-ui/app.css` and `detail.css` pixel-perfectly. No utility framework (Tailwind) or CSS-in-JS |

## Constraints

- The Vue app must **visually replicate the existing design exactly** while using modern tooling.
- Source CSS reference: `docs/current-ui/app.css` + `docs/current-ui/detail.css`.
