import { defineConfig, type Plugin } from 'vite';
import { fileURLToPath } from 'node:url';

function traceHistoryFallback(): Plugin {
  return {
    name: 'trace-history-fallback',
    configureServer(server) {
      server.middlewares.use((request, _response, next) => {
        const acceptsHtml = request.headers.accept?.includes('text/html');
        if (!request.url || !acceptsHtml) {
          next();
          return;
        }

        const url = new URL(request.url, 'http://localhost');
        if (url.pathname === '/trace' || url.pathname.startsWith('/trace/')) {
          request.url = `/trace.html${url.search}`;
        }
        next();
      });
    },
  };
}

export default defineConfig({
  plugins: [traceHistoryFallback()],
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
