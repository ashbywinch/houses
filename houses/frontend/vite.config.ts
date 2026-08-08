/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Dev server is PLAIN HTTP on :5173. The phone browser's HTTPS-First
// mode tries https:// first, fails, and falls back to http — the
// designed behavior. Serving https on this port (server.https) made
// vite https-only, breaking every http bookmark with
// ERR_EMPTY_RESPONSE. Keep it http; HTTPS-First falls back cleanly to
// a plain-http server (it only hard-fails when the server sends
// garbage bytes on the TLS attempt, which doesn't happen with a clean
// http listener).
export default defineConfig({
  plugins: [vue()],
  server: {
    allowedHosts: [
      '.sslip.io',
      '.nip.io',
    ],
    proxy: {
      '/api': {
        target: 'http://localhost:8765',
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
