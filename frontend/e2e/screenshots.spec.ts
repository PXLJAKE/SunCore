// SPDX-License-Identifier: Apache-2.0
// Documentation screenshots from the real demo mode (simulator, scenario summer_day, demo
// started at 09:30 by playwright.config.ts). Nothing is mocked.
// Run: SCREENSHOTS_DIR=../docs/assets/screenshots npx playwright test screenshots
import { expect, test } from '@playwright/test';
import { STEP_IDS } from './helpers';

const dir = process.env.SCREENSHOTS_DIR;
test.skip(!dir, 'set SCREENSHOTS_DIR to take screenshots');

const shots = [
  { view: 'now', path: '/', scheme: 'light', locale: 'en-US' },
  { view: 'now', path: '/', scheme: 'dark', locale: 'en-US' },
  { view: 'now', path: '/', scheme: 'light', locale: 'de-DE' },
  { view: 'now', path: '/', scheme: 'dark', locale: 'de-DE' },
  { view: 'settings', path: '/#/settings', scheme: 'light', locale: 'en-US' },
  { view: 'settings', path: '/#/settings', scheme: 'light', locale: 'de-DE' },
] as const;

for (const shot of shots) {
  test(`${shot.view} ${shot.scheme} ${shot.locale}`, async ({ browser }, info) => {
    test.skip(info.project.name === 'desktop', 'docs use phone and tablet');
    test.skip(shot.view === 'settings' && info.project.name === 'phone', 'tablet only');
    const ctx = await browser.newContext({
      colorScheme: shot.scheme,
      locale: shot.locale,
      viewport: info.project.use.viewport ?? { width: 1024, height: 768 },
      hasTouch: info.project.use.hasTouch,
    });
    const page = await ctx.newPage();
    await page.goto(shot.path);
    await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
    if (shot.view === 'settings') {
      await expect(page.getByTestId('overview')).toBeVisible();
    }
    // Let the live feed deliver a few updates before the capture.
    await page.waitForTimeout(1500);
    const name = `${shot.view}-${info.project.name}-${shot.scheme}-${shot.locale.slice(0, 2)}`;
    await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
    await ctx.close();
  });
}

// Wizard steps of the fully configured demo for the docs. Long pages, so tablet only
// (skill demo-screenshots: keep each PNG under 300 kB); light en/de for every step,
// dark en/de for the device gallery and the summary.
const wizardShots = [
  ...(['en-US', 'de-DE'] as const).flatMap((locale) =>
    STEP_IDS.map((step) => ({ step, scheme: 'light' as const, locale })),
  ),
  ...(['en-US', 'de-DE'] as const).flatMap((locale) =>
    (['devices', 'summary'] as const).map((step) => ({ step, scheme: 'dark' as const, locale })),
  ),
];

for (const { step, scheme, locale } of wizardShots) {
  test(`wizard ${step} ${scheme} ${locale}`, async ({ browser }, info) => {
    test.skip(info.project.name !== 'tablet', 'wizard pages are long: tablet only');
    const ctx = await browser.newContext({
      colorScheme: scheme,
      locale,
      viewport: info.project.use.viewport ?? { width: 1024, height: 768 },
      hasTouch: info.project.use.hasTouch,
    });
    const page = await ctx.newPage();
    await page.goto(`/#/setup/${step}`);
    await expect(page.getByTestId('wizard')).toHaveAttribute('data-step', step);
    await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
    // The action bar is sticky; a full-page capture would freeze it mid-page.
    await page.addStyleTag({ content: '.actions { position: static !important; }' });
    await page.waitForTimeout(1500);
    const name = `wizard-${step}-${info.project.name}-${scheme}-${locale.slice(0, 2)}`;
    await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
    await ctx.close();
  });
}
