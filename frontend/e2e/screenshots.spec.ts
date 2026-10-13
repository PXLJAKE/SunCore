// SPDX-License-Identifier: Apache-2.0
// Documentation screenshots from the real demo mode (simulator, scenario summer_day, demo
// started at 09:30 by playwright.config.ts). Nothing is mocked.
// Run: SCREENSHOTS_DIR=../docs/assets/screenshots npx playwright test screenshots
import { expect, test } from '@playwright/test';
import { resetDemo, STEP_IDS } from './helpers';

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

const switchDevice = (id: string, name: string, priority: number) => ({
  id,
  template_id: 'generic.switch',
  name,
  kind: 'switch',
  entities: { switch: { entity_id: 'switch.sim_pool_pump', invert_sign: false } },
  power_model: { kind: 'nominal', nominal_w: 750 },
  control: { kind: 'on_off', role: 'switch' },
  priority,
  min_run_s: 300,
  min_pause_s: 300,
});

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
      await expect(page.locator('form')).toBeVisible();
    }
    // Let the live feed deliver a few updates before the capture.
    await page.waitForTimeout(1500);
    const name = `${shot.view}-${info.project.name}-${shot.scheme}-${shot.locale.slice(0, 2)}`;
    await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
    await ctx.close();
  });
}

// Wizard steps (light/dark, en/de) for the docs; phone and tablet only.
for (const scheme of ['light', 'dark'] as const) {
  for (const locale of ['en-US', 'de-DE'] as const) {
    for (const step of STEP_IDS) {
      test(`wizard ${step} ${scheme} ${locale}`, async ({ browser, request }, info) => {
        test.skip(info.project.name === 'desktop', 'docs use phone and tablet');
        await resetDemo(request, { wizardDone: false });
        await request.put('/api/v1/config/devices', {
          data: {
            schema_version: 1,
            devices: [
              switchDevice('pool_pump', 'Pool pump', 0),
              switchDevice('heating_rod', 'Heating rod', 1),
              switchDevice('wallbox', 'Wallbox', 2),
            ],
          },
        });
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
        await page.waitForTimeout(1500);
        const name = `wizard-${step}-${info.project.name}-${scheme}-${locale.slice(0, 2)}`;
        await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
        await ctx.close();
        await resetDemo(request);
      });
    }
  }
}
