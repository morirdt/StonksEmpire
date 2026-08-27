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
    // One worker per test file, each importing the whole jsdom + MUI module
    // graph from a Windows drive, is enough concurrent cold-start IO that some
    // of them miss the pool's handshake timeout on a 16-core machine. The run
    // then fails with "Failed to start forks worker" and *silently skips that
    // file's tests* while reporting the file as passed — which is how a suite
    // loses fourteen tests without anything turning red. Capping the pool
    // trades a little wall time for a run that actually runs everything.
    maxWorkers: 4,
  },
});
