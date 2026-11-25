// SPDX-License-Identifier: Apache-2.0
// The history view against the demo (90 days of deterministic history, simulated clock).
import { expect, test } from '@playwright/test';
import { expectNoAxeViolations } from './helpers';

// A week that lies completely inside the demo's history (the demo's "today" is simulated).
const WEEK = '#/history?period=week&date=2026-06-10';

test.describe('History view', () => {
  test('shows tiles, charts, text alternatives and the device table', async ({ page }) => {
    await page.goto(WEEK);
    await expect(
      page.getByRole('heading', { name: 'History and analysis', level: 2 }),
    ).toBeVisible();
    const tiles = page.getByTestId('history-tiles');
    await expect(tiles).toBeVisible();
    for (const key of ['autarky', 'self_consumption', 'cost', 'savings_no_pv', 'savings_pv_only']) {
      await expect(tiles.getByTestId(`tile-${key}`)).toContainText(/\d/);
    }
    await expect(page.getByTestId('history-period')).toHaveAttribute('data-key', '2026-W24');
    for (const chart of ['energy', 'cost', 'sankey', 'heatmap', 'cycles', 'skill']) {
      const el = page.getByTestId(`chart-${chart}`);
      await expect(el).toHaveAttribute('role', 'img');
      await expect(el.locator('canvas').first()).toBeVisible();
    }
    await expect(page.getByTestId('history-attribution')).toContainText('saved');
    // Text alternative: the energy table has one row per day.
    await page.getByTestId('history-energy').getByText('Chart data as a table').click();
    await expect(page.getByTestId('table-energy').getByRole('row')).toHaveCount(8);
    await expect(page.getByTestId('device-row').first()).toBeVisible();
  });

  test('navigates periods and keeps the state in the URL', async ({ page }) => {
    await page.goto(WEEK);
    await expect(page.getByTestId('history-period')).toHaveAttribute('data-key', '2026-W24');
    await page.getByTestId('period-prev').click();
    await expect(page.getByTestId('history-period')).toHaveAttribute('data-key', '2026-W23');
    await expect(page).toHaveURL(/period=week&date=2026-06-07/);
    await page.getByTestId('period-next').click();
    await expect(page.getByTestId('history-period')).toHaveAttribute('data-key', '2026-W24');
    await page.getByTestId('period-month').click();
    await expect(page.getByTestId('history-period')).toHaveAttribute('data-key', '2026-06');
    await expect(page.getByTestId('period-month')).toHaveAttribute('aria-pressed', 'true');
    await expect(page).toHaveURL(/period=month/);
    // Back to "today" (server clock): the period is still running, so there is no next period.
    await page.getByTestId('period-today').click();
    await expect(page.getByTestId('badge-in-progress')).toBeVisible();
    await expect(page.getByTestId('period-next')).toBeDisabled();
  });

  test('opens a shared link and links a day to the plan chart', async ({ page }) => {
    await page.goto('/#/history?period=day&date=2026-06-10');
    await expect(page.getByTestId('history-period')).toHaveAttribute('data-key', '2026-06-10');
    await expect(page.getByTestId('plan-link')).toHaveAttribute('href', '#/plan?day=2026-06-10');
    await page.getByTestId('period-week').click();
    await page.getByTestId('history-energy').getByText('Chart data as a table').click();
    await expect(page.getByTestId('plan-link-day').first()).toHaveAttribute(
      'href',
      /^#\/plan\?day=2026-06-/,
    );
  });

  test('offers CSV for every table and PNG for the charts', async ({ page }) => {
    await page.goto(WEEK);
    await expect(page.getByTestId('chart-sankey').locator('canvas').first()).toBeVisible();
    for (const id of ['energy', 'cost', 'sankey', 'heatmap', 'cycles', 'skill', 'devices']) {
      const href = await page.getByTestId(`csv-${id}`).getAttribute('href');
      expect(href).toContain('format=csv');
      expect(href).toContain('/api/v1/history/');
      const res = await page.request.get(href!);
      expect(res.ok()).toBe(true);
      expect(res.headers()['content-type']).toContain('text/csv');
      expect((await res.text()).split('\n')[0]).toContain(',');
    }
    const download = page.waitForEvent('download');
    await page.getByTestId('chart-energy-png').click();
    expect((await download).suggestedFilename()).toMatch(/^suncore-energy-.*\.png$/);
  });

  test('says so when there is nothing to show yet', async ({ page }) => {
    await page.goto('/#/history?period=week&date=2020-01-08');
    await expect(page.getByTestId('history-empty')).toContainText('Collecting data');
    await expect(page.getByTestId('history-tiles')).toHaveCount(0);
  });

  test('says so when the time-series database is missing', async ({ page }) => {
    await page.route('**/api/v1/history/summary*', (route) =>
      route.fulfill({ status: 503, json: { detail: 'api.error.history_unavailable' } }),
    );
    await page.goto(WEEK);
    await expect(page.getByTestId('history-unavailable')).toBeVisible();
  });

  test('marks incomplete days', async ({ page }) => {
    await page.route('**/api/v1/history/summary*', async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      body.days_complete = body.days_with_data - 1;
      body.complete = false;
      body.buckets[2].complete = false;
      await route.fulfill({ response: res, json: body });
    });
    await page.goto(WEEK);
    await expect(page.getByTestId('badge-incomplete')).toContainText('1 day incomplete');
    await expect(page.getByTestId('history-incomplete-note')).toBeVisible();
    await page.getByTestId('history-energy').getByText('Chart data as a table').click();
    await expect(page.getByTestId('table-energy').getByText('incomplete')).toHaveCount(1);
  });

  test('keeps the other sections when one fails', async ({ page }) => {
    await page.route('**/api/v1/history/devices*', (route) =>
      route.fulfill({ status: 422, json: { detail: 'api.error.history_range_invalid' } }),
    );
    await page.goto(WEEK);
    await expect(page.getByTestId('history-devices').getByRole('alert')).toBeVisible();
    await expect(page.getByTestId('chart-energy').locator('canvas').first()).toBeVisible();
  });

  test('has no horizontal scroll', async ({ page }) => {
    await page.goto(WEEK);
    await expect(page.getByTestId('chart-heatmap').locator('canvas').first()).toBeVisible();
    const { scroll, client } = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(scroll).toBeLessThanOrEqual(client);
  });

  test('is in German for a German browser', async ({ browser }) => {
    const ctx = await browser.newContext({ locale: 'de-DE' });
    const page = await ctx.newPage();
    await page.goto(WEEK);
    await expect(
      page.getByRole('heading', { name: 'Verlauf und Analyse', level: 2 }),
    ).toBeVisible();
    await expect(page.getByTestId('tile-autarky')).toContainText('Autarkie');
    await expect(page.getByTestId('period-month')).toHaveText('Monat');
    await expect(page.getByTestId('history-period').getByRole('heading')).toContainText('Woche 24');
    await ctx.close();
  });

  for (const scheme of ['light', 'dark'] as const) {
    test(`has no axe violations (${scheme})`, async ({ browser }) => {
      const ctx = await browser.newContext({ colorScheme: scheme });
      const page = await ctx.newPage();
      await page.goto(WEEK);
      for (const chart of ['energy', 'sankey', 'heatmap', 'skill']) {
        await expect(page.getByTestId(`chart-${chart}`).locator('canvas').first()).toBeVisible();
      }
      await expect(page.getByTestId('device-row').first()).toBeVisible();
      await expectNoAxeViolations(page);
      await ctx.close();
    });
  }
});
