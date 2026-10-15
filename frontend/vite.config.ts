import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const apiUrl = env.API_URL || 'http://localhost:8000';

  return {
    plugins: [react()],
    server: {
      host: '0.0.0.0',
      port: 8501,
      strictPort: true,
      proxy: {
        '/api': {
          target: apiUrl,
          changeOrigin: false,
          secure: false,
        },
      },
    },
    preview: {
      host: '0.0.0.0',
      port: 8501,
      strictPort: true,
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
    },
  };
});
