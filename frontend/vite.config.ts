import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        // 可由 ERP_API_TARGET 环境变量覆盖 (端口冲突时脚本自动降级用)
        target: process.env.ERP_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
        // WebSocket 升级支持
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    chunkSizeWarningLimit: 1500,
  },
})
