import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build output is committed to git (see app/static/dist/.gitkeep note) since
// end-user machines never run npm - the launcher's auto-update just re-syncs
// whatever's in the repo, so the built assets have to already be there.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../app/static/dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/images': 'http://localhost:8000',
    },
  },
})
