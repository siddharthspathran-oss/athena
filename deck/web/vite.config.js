// Athena Deck's board: built into ../ui/dist, which deck/server.py serves at /ui/dist/.
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/ui/dist/',
  build: { outDir: '../ui/dist', emptyOutDir: true, chunkSizeWarningLimit: 900 },
  server: { proxy: { '/api': 'http://127.0.0.1:8430', '/ws': { target: 'ws://127.0.0.1:8430', ws: true } } },
})
