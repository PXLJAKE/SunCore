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
        // The message catalogues and the ICU formatter change independently of the views and are
        // needed on first paint anyway: one cacheable chunk instead of riding in a shared one.
        manualChunks(id: string): string | undefined {
          if (
            id.includes('/node_modules/intl-messageformat') ||
            id.includes('/node_modules/@formatjs')
          ) {
            return 'icu';
          }
          if (id.includes('/src/lib/i18n/locales/')) return 'catalogues';
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
  },
});
