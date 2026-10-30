import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react(), tailwindcss()],
    // Map API requests through Vite during local development, avoiding CORS.
    server: {
      proxy: {
        '/api': {
          target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
    // maplibre-gl is ~1.1 MB minified (≈290 KB gzip) and already lands in its own chunk
    build: { chunkSizeWarningLimit: 1200 },
  }
})
