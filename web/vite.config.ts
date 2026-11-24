import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const host = env.VITE_HOST || env.FRONTEND_HOST || process.env.FRONTEND_HOST || '127.0.0.1'
  const rawPort = env.VITE_PORT || env.FRONTEND_PORT || process.env.FRONTEND_PORT || '15173'
  const port = Number.parseInt(rawPort, 10) || 15173
  const backendPort = env.VITE_BACKEND_PORT || env.BACKEND_PORT || process.env.BACKEND_PORT || '18141'
  const apiTarget = env.VITE_API_TARGET || process.env.VITE_API_TARGET || `http://127.0.0.1:${backendPort}`

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      host,
      port,
      strictPort: true,
      proxy: {
        '/api/v1': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
    preview: {
      host,
      port,
      strictPort: true,
    },
  }
})
