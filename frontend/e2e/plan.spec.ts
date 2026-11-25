// SPDX-License-Identifier: Apache-2.0
import { expect, test } from '@playwright/test';
import { expectNoAxeViolations, openCard } from './helpers';

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
    const x = box.width * 0.45;
    await expect
      .poll(
        async () => {
          // Hover on a desktop, tap on a phone: both end in a click on the same spot.
          await chart.click({ position: { x, y: 260 } });
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
    // Both cards start collapsed; the credit stays visible below them.
    await openCard(page, 'fc-card');
    await openCard(page, 'fc-ml-card');
    // The wide tables scroll inside their cards; the page itself never scrolls sideways.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    await expect(panel.getByTestId('fc-accuracy')).toBeVisible();
    await expect(panel.getByTestId('fc-weights')).toContainText('equal');
    await expect(panel.getByTestId('fc-calibration')).toContainText('No history yet');
    await expect(panel.getByTestId('fc-ml-mode')).toContainText('comparison only');
    await expect(panel.getByTestId('fc-ml-in-use')).toContainText('the profiles');
    const credit = panel.getByTestId('open-meteo-attribution');
    await expect(credit).toContainText('CC BY 4.0');
    await expect(credit.getByRole('link', { name: 'Open-Meteo.com' })).toHaveAttribute(
      'href',
      'https://open-meteo.com/',
    );
  });

  test('opens a day from the history link: measured overlay, Gantt, exports and table', async ({
    page,
  }) => {
    await page.goto('/#/plan?day=2026-06-20');
    await expect(page.getByRole('heading', { level: 3, name: /20/ }).first()).toBeVisible();
    await expect(page.getByTestId('plan-chart').locator('canvas').first()).toBeVisible();
    await expect(page.getByTestId('plan-snapshot')).toBeVisible();
    await expect(page.getByTestId('csv-plan')).toHaveAttribute('href', /plan\/at\?ts=.*format=csv/);
    await expect(page.getByTestId('csv-actual')).toHaveAttribute('href', /history\/series/);
    await expect(page.getByTestId('range-day')).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByTestId('range-24')).toHaveAttribute('aria-pressed', 'false');
    // Measured columns in the data table, device schedule as text.
    await page.getByTestId('plan-table').getByText('Chart data as a table').click();
    await expect(page.getByTestId('plan-table').getByText('Solar (measured)')).toBeVisible();
    await page.getByTestId('plan-schedule').getByText('Device schedule as text').click();
    await expect(page.getByTestId('schedule-row').first()).toBeVisible();
    // Back to the current plan.
    await page.getByTestId('back-to-live').click();
    await expect(page).toHaveURL(/#\/plan$/);
    await expect(page.getByTestId('range-24')).toHaveAttribute('aria-pressed', 'true');
  });

  test('opens the plan of a decision time and marks it', async ({ page }) => {
    await page.goto('/#/plan?at=2026-06-20T12:00:00%2B02:00');
    await expect(page.getByRole('heading', { level: 3, name: /Plan as of/ })).toBeVisible();
    await expect(page.getByTestId('plan-snapshot')).toContainText('Plan computed');
    await expect(page.getByTestId('plan-chart').locator('canvas').first()).toBeVisible();
    await expect(page.getByTestId('range-24')).toHaveAttribute('aria-pressed', 'false');
  });

  test('says so when no plan is stored for the time', async ({ page }) => {
    await page.goto('/#/plan?day=2026-05-01');
    await expect(page.getByTestId('plan-snapshot-missing')).toBeVisible();
    await expect(page.getByTestId('plan-chart')).toBeVisible();
  });

  test('compares two plans', async ({ page }) => {
    await page.goto('/#/plan?at=2026-06-20T12:00:00%2B02:00&compare=2026-06-19T12:00:00%2B02:00');
    await expect(page.getByTestId('plan-compare-summary')).toContainText('differ');
    await expect(page.getByTestId('csv-compare')).toBeVisible();
    await expect(page.getByTestId('plan-compare-table')).toBeVisible();
    await page.getByTestId('compare-clear').click();
    await expect(page.getByTestId('plan-compare-summary')).toHaveCount(0);
  });

  test('zooms and pans with the keyboard and the buttons', async ({ page }) => {
    await page.goto('/#/plan?day=2026-06-20');
    const frame = page.getByTestId('plan-chart-frame');
    await expect(page.getByTestId('plan-chart').locator('canvas').first()).toBeVisible();
    await expect(page.getByTestId('zoom-reset')).toBeDisabled();
    await frame.focus();
    await page.keyboard.press('+');
    await expect(page.getByTestId('zoom-status')).toContainText('Showing');
    await page.keyboard.press('ArrowRight');
    await page.getByTestId('zoom-out').click();
    await page.keyboard.press('0');
    await expect(page.getByTestId('zoom-status')).toHaveText('');
    await expect(page.getByTestId('zoom-reset')).toBeDisabled();
  });

  test('jumps to a day with the date field', async ({ page }) => {
    await page.goto('/#/plan');
    await page.getByTestId('range-day').click();
    await expect(page).toHaveURL(/#\/plan\?day=/);
    await page.getByTestId('day-prev').click();
    await expect(page).toHaveURL(/#\/plan\?day=2026-06-20/);
    await expect(page.getByTestId('day-input')).toHaveValue('2026-06-20');
  });

  test('has no axe violations on a day with a comparison', async ({ page }) => {
    await page.goto('/#/plan?day=2026-06-20&compare=2026-06-19T12:00:00%2B02:00');
    await expect(page.getByTestId('plan-chart').locator('canvas').first()).toBeVisible();
    await expect(page.getByTestId('plan-compare-summary')).toBeVisible();
    await expectNoAxeViolations(page);
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
      await openCard(page, 'fc-card');
      await openCard(page, 'fc-ml-card');
      await expect(page.getByTestId('fc-accuracy')).toBeVisible();
      await expectNoAxeViolations(page);
      await ctx.close();
    });
  }
});
