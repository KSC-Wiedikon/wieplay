import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The FastAPI backend serves /api on the same origin in production.
// In dev, Vite proxies it; the Host header is kept so the backend builds
// redirects and cookies for the dev server's origin.
const target = process.env.NOMAL_API_URL ?? 'http://127.0.0.1:8077'
const api = { '/api': { target, changeOrigin: false } }

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: api },
  preview: { proxy: api },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
