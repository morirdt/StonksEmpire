/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  resolve: {
    // '@/lib/api/client' beats '../../../lib/api/client' once features nest.
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    // Bound to all interfaces so the container's dev server is reachable.
    host: true,
    watch: {
      // The working tree is on a Windows drive; inotify does not fire across
      // the 9p/drvfs boundary, so the watcher has to poll.
      usePolling: true,
      interval: 300,
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
