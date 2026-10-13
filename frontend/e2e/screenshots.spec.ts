// SPDX-License-Identifier: Apache-2.0
// Documentation screenshots from the real demo mode (simulator, scenario summer_day, demo
// started at 09:30 by playwright.config.ts). Nothing is mocked.
// Run: SCREENSHOTS_DIR=../docs/assets/screenshots npx playwright test screenshots
import { expect, test } from '@playwright/test';

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
    // The long settings form stays under the 300 kB budget only on the tablet.
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
      await expect(page.locator('form')).toBeVisible();
    }
    // Let the live feed deliver a few updates before the capture.
    await page.waitForTimeout(1500);
    const name = `${shot.view}-${info.project.name}-${shot.scheme}-${shot.locale.slice(0, 2)}`;
    await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
    await ctx.close();
  });
}
