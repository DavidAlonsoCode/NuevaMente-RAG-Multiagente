import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const apiUrl = env.API_URL || 'http://localhost:8000';
  const proxy = { '/api': { target: apiUrl, changeOrigin: false } };

  return {
    plugins: [react()],
    server: {
      host: '127.0.0.1',
      port: 8501,
      strictPort: true,
      proxy,
    },
    preview: {
      host: '127.0.0.1',
      port: 8501,
      strictPort: true,
      proxy,
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
      assetsInlineLimit: 0,
    },
    test: { environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'], restoreMocks: true },
  };
});
