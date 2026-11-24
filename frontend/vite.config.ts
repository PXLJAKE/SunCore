// SPDX-License-Identifier: Apache-2.0
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';

// `base: './'` keeps every asset URL relative so the Home Assistant ingress
// base path (dynamic, set by the Supervisor) never matters.
export default defineConfig({
  base: './',
  plugins: [svelte()],
  build: {
    target: 'es2022',
    sourcemap: false,
    rollupOptions: {
      output: {
        // The message catalogues and the ICU formatter change independently of the views: separate
        // cacheable chunks instead of riding in a shared one.
        manualChunks(id: string): string | undefined {
          if (
            id.includes('/node_modules/intl-messageformat') ||
            id.includes('/node_modules/@formatjs')
          ) {
            return 'icu';
          }
          // The parameter help (expert area only) loads on demand, not on first paint.
          if (id.includes('/src/lib/i18n/locales/params.')) return 'catalogue-params';
          // One chunk per main catalogue: English is eager, the others load on demand.
          const main = /\/src\/lib\/i18n\/locales\/(en|de)\.json$/.exec(id);
          if (main) return `catalogue-${main[1]}`;
          // ECharts and its canvas layer ride along with the plan view only (dynamic import).
          if (id.includes('/node_modules/echarts') || id.includes('/node_modules/zrender')) {
            return 'echarts';
          }
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    // Dev: proxy API and live feed to the demo backend on loopback.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8099', ws: true },
      '/health': 'http://127.0.0.1:8099',
    },
  },
  resolve: {
    conditions: ['browser'],
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'eslint/**/*.test.js'],
    globals: false,
    setupFiles: ['src/test-setup.ts'],
  },
});
