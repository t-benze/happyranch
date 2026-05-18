/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

function resolveDaemonPort(): number {
  const home = process.env.GRASSLAND_DAEMON_HOME || path.join(os.homedir(), '.grassland');
  const portFile = path.join(home, 'daemon.port');
  try {
    const raw = fs.readFileSync(portFile, 'utf-8').trim();
    const port = parseInt(raw, 10);
    if (!Number.isNaN(port) && port > 0) return port;
  } catch {
    /* fall through to default */
  }
  return 8765;
}

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${resolveDaemonPort()}`,
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    css: false,
  },
});
