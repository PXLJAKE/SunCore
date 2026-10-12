// SPDX-License-Identifier: Apache-2.0
// Deterministic documentation screenshots: the live feed is replaced by a fixed daytime frame
// (the demo clock starts at night). Run: SCREENSHOTS_DIR=../docs/assets/screenshots npx playwright test screenshots
import { expect, test } from '@playwright/test';

const dir = process.env.SCREENSHOTS_DIR;
test.skip(!dir, 'set SCREENSHOTS_DIR to take screenshots');

const q = (value: number) => ({ value, quality: 'ok' });
const snapshot = {
  type: 'snapshot',
  flows: {
    grid_w: q(-1800),
    pv_w: q(4200),
    battery_w: q(1500),
    house_w: q(900),
    battery_soc_pct: q(64),
    ts: '2026-06-21T12:00:00Z',
  },
  health: {
    ok: true,
    ha_state: 'connected',
    ha_last_update_age_s: 1,
    db_ok: true,
    version: 'demo',
  },
  decisions: [
    {
      ts: '2026-06-21T11:58:00Z',
      kind: 'shadow',
      mode: 'shadow',
      intent: {
        id: 'i1',
        asset_id: 'battery',
        origin: 'planner',
        action: { kind: 'power_setpoint', power_w: 1500 },
        reasons: [],
      },
      reasons: [{ key: 'safety.shadow_mode', args: {} }],
    },
  ],
};

for (const scheme of ['light', 'dark'] as const) {
  for (const locale of ['en-US', 'de-DE'] as const) {
    test(`now and settings ${scheme} ${locale}`, async ({ browser }, info) => {
      const ctx = await browser.newContext({
        colorScheme: scheme,
        locale,
        viewport: info.project.use.viewport ?? { width: 1280, height: 800 },
        hasTouch: info.project.use.hasTouch,
      });
      const page = await ctx.newPage();
      await page.routeWebSocket('**/api/v1/live', (ws) => ws.send(JSON.stringify(snapshot)));
      await page.goto('/');
      await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
      await page.waitForTimeout(300);
      const name = `${info.project.name}-${scheme}-${locale.slice(0, 2)}`;
      await page.screenshot({ path: `${dir}/now-${name}.png`, fullPage: true });
      await page.goto('/#/settings');
      await expect(page.locator('form')).toBeVisible();
      await page.waitForTimeout(500);
      await page.screenshot({ path: `${dir}/settings-${name}.png`, fullPage: true });
      await ctx.close();
    });
  }
}
