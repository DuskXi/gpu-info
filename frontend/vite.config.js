import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        ws: true
      },
      '/status.json': 'http://127.0.0.1:8001',
      '/status.md': 'http://127.0.0.1:8001',
      '/metrics': 'http://127.0.0.1:8001'
    }
  }
})
