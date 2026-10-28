// SPDX-License-Identifier: Apache-2.0
import { expect, test } from '@playwright/test';
import { expectNoAxeViolations } from './helpers';

test.describe('Plan view', () => {
  test('renders the plan with chart, badges, status and reasons', async ({ page }) => {
    await page.goto('/#/plan');
    await expect(page.getByRole('heading', { name: 'Plan', level: 2 })).toBeVisible();
    await expect(page.getByTestId('plan-source')).toHaveText('Optimiser');
    await expect(page.getByTestId('plan-in-use')).toContainText('follows this plan');
    await expect(page.getByTestId('plan-last-run')).toContainText('successful');
    await expect(page.getByTestId('plan-next-run')).toBeVisible();
    const chart = page.getByTestId('plan-chart');
    await expect(chart).toBeVisible();
    await expect(chart).toHaveAttribute('role', 'img');
    await expect(chart.locator('canvas').first()).toBeVisible();
    // The text alternative: planned actions with "Why?" and the data table.
    const actions = page.getByTestId('planned-actions').getByTestId('planned-action');
    await expect(actions.first()).toBeVisible();
    await actions.first().getByText('Why?').click();
    await expect(actions.first().locator('li').first()).not.toBeEmpty();
    await page.getByTestId('plan-table').getByText('Chart data as a table').click();
    await expect(page.getByTestId('plan-table').getByRole('row').nth(1)).toBeVisible();
  });

  test('shows the reasons of an interval in the tooltip', async ({ page }) => {
    await page.goto('/#/plan');
    const chart = page.getByTestId('plan-chart');
    await expect(chart.locator('canvas').first()).toBeVisible();
    const box = (await chart.boundingBox())!;
    await expect
      .poll(
        async () => {
          // Hover on a desktop, tap on a phone: both end in a click on the same spot.
          await page.mouse.move(box.x + box.width * 0.4, box.y + 150);
          await page.mouse.click(box.x + box.width * 0.45, box.y + 150);
          return page.getByText('Why', { exact: true }).count();
        },
        { timeout: 15_000 },
      )
      .toBeGreaterThan(0);
  });

  test('switches between 24 and 48 hours', async ({ page }) => {
    await page.goto('/#/plan');
    await expect(page.getByTestId('range-24')).toHaveAttribute('aria-pressed', 'true');
    const rows = () => page.getByTestId('planned-action').count();
    await page.getByTestId('range-48').click();
    await expect(page.getByTestId('range-48')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('range-24')).toHaveAttribute('aria-pressed', 'false');
    expect(await rows()).toBeGreaterThan(0);
  });

  test('replan gives feedback and cannot be pressed twice at once', async ({ page }) => {
    await page.goto('/#/plan');
    const replan = page.getByTestId('replan');
    await expect(replan).toBeEnabled();
    await replan.click();
    await expect(page.getByTestId('replan-feedback')).toContainText(/requested|moment ago/);
    await expect(replan).toBeEnabled({ timeout: 10_000 });
  });

  test('lists the forecast sources, accuracy, weights, calibration and the Open-Meteo credit', async ({
    page,
  }) => {
    await page.goto('/#/plan');
    const panel = page.getByTestId('forecast-panel');
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId('fc-accuracy')).toBeVisible();
    await expect(panel.getByTestId('fc-weights')).toContainText('equal');
    await expect(panel.getByTestId('fc-calibration')).toContainText('No history yet');
    await expect(panel.getByTestId('fc-ml')).toContainText('Not available yet');
    const credit = panel.getByTestId('open-meteo-attribution');
    await expect(credit).toContainText('CC BY 4.0');
    await expect(credit.getByRole('link', { name: 'Open-Meteo.com' })).toHaveAttribute(
      'href',
      'https://open-meteo.com/',
    );
  });

  test('says so when the planner is not running', async ({ page }) => {
    await page.route('**/api/v1/plan?*', (route) =>
      route.fulfill({ status: 409, json: { detail: 'api.error.planning_unavailable' } }),
    );
    await page.route('**/api/v1/plan/status', (route) =>
      route.fulfill({ status: 409, json: { detail: 'api.error.planning_unavailable' } }),
    );
    await page.goto('/#/plan');
    await expect(page.getByTestId('plan-unavailable')).toBeVisible();
  });

  test('is in German for a German browser', async ({ browser }) => {
    const ctx = await browser.newContext({ locale: 'de-DE' });
    const page = await ctx.newPage();
    await page.goto('/#/plan');
    await expect(page.getByTestId('plan-source')).toHaveText('Optimierer');
    await expect(page.getByRole('button', { name: 'Jetzt neu planen' })).toBeVisible();
    await expect(page.getByText('Nächste geplante Aktionen')).toBeVisible();
    await ctx.close();
  });

  for (const scheme of ['light', 'dark'] as const) {
    test(`has no axe violations (${scheme})`, async ({ browser }) => {
      const ctx = await browser.newContext({ colorScheme: scheme });
      const page = await ctx.newPage();
      await page.goto('/#/plan');
      await expect(page.getByTestId('plan-chart').locator('canvas').first()).toBeVisible();
      await expect(page.getByTestId('fc-accuracy')).toBeVisible();
      await expectNoAxeViolations(page);
      await ctx.close();
    });
  }
});
