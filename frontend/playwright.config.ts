// SPDX-License-Identifier: Apache-2.0
import { defineConfig, devices } from '@playwright/test';

// SUNCORE_E2E_PORT lets parallel checkouts (worktrees) run side by side.
const PORT = Number(process.env.SUNCORE_E2E_PORT ?? 8099);

// E2E runs against the real backend in demo mode, serving the production build of the SPA.
export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    locale: 'en-US',
  },
  webServer: {
    command: `npm run build && cd .. && SUNCORE_PORT=${PORT} SUNCORE_DEMO=1 SUNCORE_DEMO_START_TIME=09:30 SUNCORE_STATIC_DIR=frontend/dist uv run python -m suncore`,
    url: `http://127.0.0.1:${PORT}/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } },
    },
    {
      name: 'phone',
      use: {
        ...devices['Pixel 7'],
        viewport: { width: 375, height: 667 },
      },
    },
    {
      name: 'tablet',
      use: { ...devices['Desktop Chrome'], viewport: { width: 768, height: 1024 }, hasTouch: true },
    },
  ],
});
