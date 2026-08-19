import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  build: {
    rollupOptions: {
      input: {
        workspace: fileURLToPath(new URL('./index.html', import.meta.url)),
        trace: fileURLToPath(new URL('./trace.html', import.meta.url)),
      },
    },
  },
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: process.env.TEXT_TO_CAD_API_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
});
