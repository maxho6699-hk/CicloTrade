import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@ciclotrade/i18n': fileURLToPath(new URL('./src/i18n', import.meta.url)),
    },
  },
  build: {
    manifest: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('lightweight-charts')) return 'charts'
          if (id.includes('lucide-react')) return 'icons'
          if (id.includes('react-dom') || id.includes('react-router') || /node_modules[/\\]react[/\\]/.test(id)) return 'react'
        },
      },
    },
  },
  server: {
    proxy: {
      '/api/rewrite': 'http://127.0.0.1:8001',
    },
  },
})
