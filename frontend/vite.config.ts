import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the API runs on :8000 and Vite proxies the API paths to it, so the
// browser talks to one origin and there is no CORS to configure. In production Caddy
// does the same job (app/deploy/Caddyfile).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/auth': 'http://127.0.0.1:8000',
      '/jobs': 'http://127.0.0.1:8000',
      '^/billing/': 'http://127.0.0.1:8000', // only the API's /billing/... paths; /billing itself is a page
      '/Health': 'http://127.0.0.1:8000',
    },
  },
})
