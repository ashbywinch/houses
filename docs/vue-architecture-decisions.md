# Vue Architecture Decisions

## Context

This project replaces Jinja2 server-rendered templates with a Vue 3 SPA that
consumes the new REST API (Phase 4). The Vue app must visually replicate the
existing design exactly while using modern tooling.

## Decisions

### 1. Composition API with `<script setup>`

**Choice:** Use `<script setup lang="ts">` for all components.

**Reason:** It is the recommended syntax for Vue 3 SFCs — less boilerplate,
better TypeScript inference, and compile-time optimizations. Every new
component starts with this syntax.

### 2. State Management: Pinia

**Choice:** Use Pinia for global application state.

**Reason:** Property data, commute results, and WebSocket updates need to be
accessible across multiple components and views. Pinia provides:
- DevTools integration for debugging state changes
- TypeScript support with typed stores
- Actions for explicit mutation patterns
- No SSR concerns (this is a browser-only SPA)

### 3. Routing: Vue Router 4

**Choice:** Vue Router with hash-mode for simplicity.

- `/` — list page (PropertyList)
- `/property/:rid` — detail page (PropertyDetail)

Hash mode avoids backend route configuration since the backend serves the
Vue app at the root.

### 4. Vite Proxy Configuration

**Choice:** Vite dev server proxies `/api` to `http://localhost:8080`.

The `vite.config.ts` proxy setting forwards all `/api` requests to the FastAPI
backend during development. No CORS configuration needed on the backend.

### 5. WebSocket Integration

**Choice:** A `useWebSocket` composable using the native WebSocket API.

The composable:
- Connects to `ws://localhost:8080/ws` on mount
- Receives property update payloads
- Updates the Pinia store on each message

No external library needed — the native WebSocket API is sufficient.

### 6. HTTP Client

**Choice:** Native `fetch()` wrapped in an api service module.

The existing codebase doesn't use axios or similar. A thin wrapper around
`fetch()` in `services/api.ts` is sufficient for the REST calls.

### 7. Component Hierarchy

```
App.vue
├── PropertyList.vue         (route: /)
│     └── PropertyCard.vue   (per property)
│           ├── CommutePill.vue
│           └── ...
└── PropertyDetail.vue       (route: /property/:rid)
      ├── LocationMap.vue
      ├── CommuteList.vue
      │     └── CommutePill.vue
      ├── SchoolsSection.vue
      └── InfoSection.vue
```

### 8. Testing

**Choice:** Vitest + @vue/test-utils.

- Unit test composables and stores in isolation
- Component tests use `mount()` / `shallowMount()` with mocked API responses
- No e2e tests for the MVP

### 9. CSS Approach

**Choice:** Scoped styles in each `.vue` file, replicating the exact CSS from
`docs/current-ui/app.css` and `docs/current-ui/detail.css`.

No utility framework (Tailwind) or CSS-in-JS. The existing stylesheets are
extracted into component-scoped styles to match the current design pixel-
perfectly.

### 10. Project Structure

```
houses/frontend/
├── src/
│   ├── main.ts
│   ├── App.vue
│   ├── router/index.ts
│   ├── stores/properties.ts
│   ├── services/api.ts
│   ├── composables/useWebSocket.ts
│   ├── types/index.ts
│   ├── views/
│   │   ├── PropertyList.vue
│   │   └── PropertyDetail.vue
│   └── components/
│       ├── PropertyCard.vue
│       ├── CommutePill.vue
│       ├── LocationMap.vue
│       ├── SchoolsSection.vue
│       └── InfoSection.vue
├── index.html
├── vite.config.ts
├── tsconfig.json
├── tsconfig.app.json
└── package.json
```
