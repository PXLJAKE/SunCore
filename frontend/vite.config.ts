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
          // One chunk per main catalogue: English is eager, the others load on demand. Other files
          // in the folder (lazy catalogues such as param texts) keep their own dynamic chunks.
          const main = /\/src\/lib\/i18n\/locales\/(en|de)\.json$/.exec(id);
          if (main) return `catalogue-${main[1]}`;
          // ECharts and its canvas layer load on demand with the plan and history views only: one
          // shared core, and per view the parts only that view registers (plan decision 6).
          if (id.includes('/node_modules/echarts') || id.includes('/node_modules/zrender')) {
            if (/\/echarts\/lib\/(chart\/(sankey|heatmap)|component\/visualMap)\//.test(id)) {
              return 'echarts-history';
            }
            if (/\/echarts\/lib\/(chart\/custom|component\/dataZoom)\//.test(id)) {
              return 'echarts-plan';
            }
            return 'echarts-core';
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
