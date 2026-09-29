import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // The phase 6b server listens on 127.0.0.1:8765 (see src/services/config.ts).
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
      // Finished videos / covers / subtitles (Range requests included).
      '/media': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
    },
  },
})
