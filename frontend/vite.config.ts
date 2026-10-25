import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const nodeModules = decodeURIComponent(new URL('./node_modules/', import.meta.url).pathname);

export default defineConfig({
  plugins: [react()],
  // Относительные пути к ассетам: приложение живёт за HA Ingress
  // под произвольным префиксом.
  base: './',
  build: { outDir: 'dist' },
  server: { proxy: { '/api': 'http://localhost:8000' } },
  test: {
    environment: 'jsdom',
    // Parity-тест импортирует замороженный ../meal-plan.jsx из корня репо:
    // голые импорты оттуда резолвим в node_modules фронтенда.
    alias: [{ find: /^(react|react-dom|lucide-react)(\/.*)?$/, replacement: `${nodeModules}$1$2` }],
  },
});
