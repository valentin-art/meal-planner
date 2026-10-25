import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  // Относительные пути к ассетам: приложение живёт за HA Ingress
  // под произвольным префиксом.
  base: './',
  build: { outDir: 'dist' },
  server: { proxy: { '/api': 'http://localhost:8000' } },
  test: { environment: 'jsdom' },
});
