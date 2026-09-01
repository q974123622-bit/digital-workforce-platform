import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const adminHistoryFallback = () => ({
  name: 'admin-history-fallback',
  configureServer(server: { middlewares: { use: (handler: (req: { url?: string }, res: unknown, next: () => void) => void) => void } }) {
    server.middlewares.use((req, _res, next) => {
      if (req.url && /^\/admin(?:\/|$)/.test(req.url) && !/\.[^/]+(?:\?|$)/.test(req.url)) {
        req.url = '/admin/index.html';
      }
      next();
    });
  },
});

export default defineConfig({
  plugins: [adminHistoryFallback(), react()],
  build: {
    rollupOptions: {
      input: {
        portal: new URL('./index.html', import.meta.url).pathname,
        admin: new URL('./admin/index.html', import.meta.url).pathname,
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test-setup.ts',
  },
});
