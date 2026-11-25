// SPDX-License-Identifier: Apache-2.0
// The shell of the design system (ADR-0019, plan phase-7 WP4): four destinations, Analysis tabs on
// the unchanged hash routes, the emergency stop in the top bar on every size, no horizontal scroll
// at 390 px, axe clean in light and dark.
import { expect, test, type Page } from '@playwright/test';
import { expectNoAxeViolations } from './helpers';

const nav = (page: Page) => page.getByRole('navigation', { name: 'Main navigation' });

test('four destinations; the hash routes keep working and mark their destination', async ({
  page,
}) => {
  await page.goto('/');
  await expect(nav(page).getByRole('link')).toHaveText(['Now', 'Plan', 'Analysis', 'Settings']);
  for (const [hash, current] of [
    ['#/', 'Now'],
    ['#/plan', 'Plan'],
    ['#/history', 'Analysis'],
    ['#/decisions', 'Analysis'],
    ['#/whatif', 'Analysis'],
    ['#/settings', 'Settings'],
    ['#/setup/pv', 'Settings'],
  ] as const) {
    await page.goto(`/${hash}`);
    await expect(nav(page).getByRole('link', { name: current, exact: true })).toHaveAttribute(
      'aria-current',
      'page',
    );
    await expect(nav(page).locator('[aria-current="page"]')).toHaveCount(1);
  }
});

test('Analysis has tabs for history and decisions; what-if only in expert mode', async ({
  page,
}) => {
  await page.goto('/#/decisions');
  const tabs = page.getByRole('navigation', { name: 'Analysis sections' });
  await expect(tabs.getByRole('link')).toHaveText(['History', 'Decision log']);
  await expect(tabs.getByRole('link', { name: 'Decision log' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await tabs.getByRole('link', { name: 'History' }).click();
  // The tab opens `#/history`; the history view then writes its state into the URL with
  // `router.replace` (canonical `#/history?period=week`). Asserting the bare hash raced that
  // replace and passed only when the view's chunk loaded late, so the test checks the settled
  // URL instead.
  await expect(page).toHaveURL(/#\/history\?period=week$/);
  await expect(page.getByRole('heading', { name: 'History', level: 2 })).toBeVisible();
  await page.getByRole('switch', { name: 'Expert mode' }).click();
  await expect(tabs.getByRole('link')).toHaveText(['History', 'Decision log', 'What-if']);
  await page.getByRole('switch', { name: 'Expert mode' }).click();
  // Not on the other destinations.
  await page.goto('/#/plan');
  await expect(page.getByTestId('analysis-tabs')).toHaveCount(0);
});

test('a device link still focuses the device card', async ({ page }) => {
  await page.goto('/#/');
  const card = page.locator('[id^="device-card-"]').first();
  const id = (await card.getAttribute('id'))!.slice('device-card-'.length);
  await page.goto(`/#/?device=${id}`);
  await expect(page.locator(`#device-card-${id}`)).toBeFocused();
});

for (const size of [
  { name: 'phone', width: 390, height: 844 },
  { name: 'desktop', width: 1280, height: 800 },
] as const) {
  test.describe(`${size.name} ${size.width} px`, () => {
    test.use({ viewport: { width: size.width, height: size.height } });

    test('the emergency stop stays in view while scrolling', async ({ page }) => {
      await page.goto('/');
      await expect(page.getByTestId('device-cards')).toBeVisible();
      await page.mouse.wheel(0, 3000);
      await page.waitForTimeout(200);
      await expect(page.getByTestId('estop')).toBeInViewport();
      await expect(page.getByRole('button', { name: 'Emergency stop' })).toBeVisible();
      await expect(nav(page)).toBeInViewport();
    });

    test('no horizontal scroll on any destination', async ({ page }) => {
      for (const hash of ['#/', '#/plan', '#/history', '#/decisions', '#/settings', '#/setup/pv']) {
        await page.goto(`/${hash}`);
        await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
        await page.waitForTimeout(300);
        const { scroll, client } = await page.evaluate(() => ({
          scroll: document.documentElement.scrollWidth,
          client: document.documentElement.clientWidth,
        }));
        expect(scroll, hash).toBeLessThanOrEqual(client);
      }
    });

    test('destinations have 44 px touch targets', async ({ page }) => {
      await page.goto('/');
      for (const link of await nav(page).getByRole('link').all()) {
        const box = (await link.boundingBox())!;
        expect(box.height).toBeGreaterThanOrEqual(44);
      }
      const estop = (await page.getByTestId('estop').boundingBox())!;
      expect(estop.height).toBeGreaterThanOrEqual(44);
    });

    for (const scheme of ['light', 'dark'] as const) {
      test(`shell has no axe violations (${scheme})`, async ({ browser }) => {
        const ctx = await browser.newContext({
          colorScheme: scheme,
          viewport: { width: size.width, height: size.height },
        });
        const page = await ctx.newPage();
        for (const hash of ['#/', '#/plan', '#/history', '#/setup/pv']) {
          await page.goto(`/${hash}`);
          await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
          if (hash === '#/plan') {
            await expect(page.getByTestId('plan-chart').locator('canvas').first()).toBeVisible();
          }
          if (hash === '#/history') {
            await expect(page.getByTestId('chart-energy').locator('canvas').first()).toBeVisible();
          }
          await expectNoAxeViolations(page);
        }
        await ctx.close();
      });
    }
  });
}
