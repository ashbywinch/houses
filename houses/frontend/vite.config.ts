/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // Vite 8.0 has a bug in ErrorOverlay where err.frame!.trim() throws
    // "W.trim is not a function" when an error has a truthy non-string frame
    // (https://github.com/vitejs/vite/issues/NEXT — not yet fixed upstream).
    // Disable the overlay so devs see the browser console instead of a blank
    // page. Remove this when upstream fixes the guard in overlay.ts.
    hmr: { overlay: false },
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.{test,spec}.{js,ts,jsx,tsx}'],
  },
})
