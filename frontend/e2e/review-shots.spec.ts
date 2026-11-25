// SPDX-License-Identifier: Apache-2.0
// Before/after screenshots for the design review stops (plan phase-7 WP4, review stop 1).
// The same set on both sides: Now, Plan, Analysis (history), Settings and one wizard step at
// phone 390x844 and desktop 1280x800, light and dark, English. Demo mode only, nothing mocked.
// Run: REVIEW_SHOTS_DIR=../docs/assets/screenshots/after-7d npx playwright test review-shots \
//        --project=desktop
import { expect, test, type Page } from '@playwright/test';

const dir = process.env.REVIEW_SHOTS_DIR;
test.skip(!dir, 'set REVIEW_SHOTS_DIR to take review screenshots');
// Optional comma-separated subset of the views, e.g. REVIEW_SHOTS_VIEWS=now (one view PR).
const only = process.env.REVIEW_SHOTS_VIEWS?.split(',').map((v) => v.trim());

const SIZES = [
  { size: 'phone', viewport: { width: 390, height: 844 }, mobile: true },
  { size: 'desktop', viewport: { width: 1280, height: 800 }, mobile: false },
] as const;

const VIEWS = [
  { view: 'now', path: '/' },
  { view: 'plan', path: '/#/plan?day=2026-06-20' },
  { view: 'plan-live', path: '/#/plan' },
  { view: 'analysis', path: '/#/history?period=month' },
  { view: 'decisions', path: '/#/decisions' },
  { view: 'whatif', path: '/#/whatif' },
  { view: 'settings', path: '/#/settings' },
  // 7-G: the settings sub-pages (after only) and the wizard steps, one per stage and the summary.
  { view: 'settings-system', path: '/#/settings/system' },
  { view: 'settings-devices', path: '/#/settings/devices' },
  { view: 'settings-ha', path: '/#/settings/ha' },
  { view: 'settings-advanced', path: '/#/settings/advanced' },
  { view: 'wizard-region', path: '/#/setup/region' },
  { view: 'wizard-grid', path: '/#/setup/grid' },
  { view: 'wizard-pv', path: '/#/setup/pv' },
  { view: 'wizard-devices', path: '/#/setup/devices' },
  { view: 'wizard-summary', path: '/#/setup/summary' },
] as const;

async function ready(page: Page, view: (typeof VIEWS)[number]['view']): Promise<void> {
  await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
  if (view === 'now') await expect(page.getByTestId('device-cards')).toBeVisible();
  if (view === 'plan' || view === 'plan-live') {
    await expect(page.getByTestId('plan-chart').locator('canvas').first()).toBeVisible();
  }
  if (view === 'decisions') await expect(page.getByTestId('log-list')).toBeVisible();
  if (view === 'whatif') await expect(page.getByTestId('whatif-form')).toBeVisible();
  if (view === 'analysis') {
    await expect(page.getByTestId('chart-energy').locator('canvas').first()).toBeVisible();
  }
  if (view === 'settings') await expect(page.getByRole('heading', { level: 2 })).toBeVisible();
  if (view.startsWith('settings-')) await expect(page.getByTestId('settings-page')).toBeVisible();
  if (view.startsWith('wizard-')) {
    const step = view.slice('wizard-'.length);
    await expect(page.getByTestId('wizard')).toHaveAttribute('data-step', step);
    if (step === 'summary') await expect(page.getByTestId('plan-preview')).toBeVisible();
  }
}

for (const s of SIZES) {
  for (const scheme of ['light', 'dark'] as const) {
    for (const v of VIEWS) {
      test(`review ${v.view} ${s.size} ${scheme}`, async ({ browser }, info) => {
        test.skip(info.project.name !== 'desktop', 'one project is enough (own viewports)');
        test.skip(!!only && !only.includes(v.view), 'not in REVIEW_SHOTS_VIEWS');
        const ctx = await browser.newContext({
          colorScheme: scheme,
          locale: 'en-US',
          viewport: s.viewport,
          deviceScaleFactor: 2,
          isMobile: s.mobile,
          hasTouch: s.mobile,
        });
        try {
          const page = await ctx.newPage();
          // The what-if view belongs to expert mode (a per-browser switch).
          if (v.view === 'whatif') {
            await page.addInitScript(() => localStorage.setItem('suncore.expert', '1'));
          }
          await page.goto(v.path);
          await ready(page, v.view);
          // Sticky and fixed bars would freeze mid-page in a full-page capture: the wizard's action
          // bar goes static, the phone's bottom bar to the end of the page.
          await page.addStyleTag({
            content:
              '.actions { position: static !important; }' +
              (s.mobile
                ? ' body { position: relative; } .appbar { position: static !important; }' +
                  ' nav.destinations { position: absolute !important; }'
                : ''),
          });
          await page.waitForTimeout(1500);
          await page.screenshot({
            path: `${dir}/${v.view}-${s.size}-${scheme}.png`,
            fullPage: true,
            animations: 'disabled',
          });
          // The device cards on their own (the Now view's grid), for a closer look.
          if (v.view === 'now') {
            await page.addStyleTag({ content: '.appbar { position: static !important; }' });
            await page.getByTestId('device-cards').screenshot({
              path: `${dir}/now-devices-${s.size}-${scheme}.png`,
              animations: 'disabled',
            });
          }
        } finally {
          await ctx.close();
        }
      });
    }
  }
}
