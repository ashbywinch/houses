/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import fs from 'node:fs'

// Dev server speaks TLS on the hostname. Chrome's HTTPS-Upgrades upgrades a
// typed hostname and does not fall back to plain HTTP, and Google only
// accepts https (or loopback) OAuth redirect URIs — so the hostname has to
// serve TLS. Plain `http://` requests to this port get no reply: use the
// printed https:// URL. Certs come from `make gen-certs` (self-signed,
// machine-local); without them the server stays plain HTTP.
const certDir = new URL('./certs/', import.meta.url)
const https = (() => {
  try {
    return {
      key: fs.readFileSync(new URL('dev.key', certDir)),
      cert: fs.readFileSync(new URL('dev.crt', certDir)),
    }
  } catch {
    // No certs/ yet — run `make gen-certs` for TLS, or use plain http.
    return undefined
  }
})()

export default defineConfig({
  plugins: [vue()],
  server: {
    https,
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
