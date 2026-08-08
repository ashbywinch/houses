/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import fs from 'node:fs'

const certDir = new URL('./certs/', import.meta.url)
const https = (() => {
  try {
    return {
      key: fs.readFileSync(new URL('dev.key', certDir)),
      cert: fs.readFileSync(new URL('dev.crt', certDir)),
    }
  } catch {
    // No certs/ yet — run `make gen-certs` or just use plain http.
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
