import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// GPU_INFO_API points the dev server at another backend, e.g. a read-only remote deployment.
const api = process.env.GPU_INFO_API || 'http://127.0.0.1:8001'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: api,
        changeOrigin: true,
        ws: true
      },
      '/status.json': api,
      '/status.md': api,
      '/metrics': api
    }
  }
})
