// SPDX-License-Identifier: Apache-2.0
// The what-if view against the demo (stored history, simulated clock): gating, a run with its
// result, cancel, delete and the form's own checks.
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { expectNoAxeViolations } from './helpers';

test.describe.configure({ mode: 'serial' });

async function clearRuns(request: APIRequestContext): Promise<void> {
  const runs = (await (await request.get('/api/v1/backtests')).json()) as { id: number }[];
  for (const r of runs) {
    await request.delete(`/api/v1/backtests/${r.id}`); // cancels a running run
    await request.delete(`/api/v1/backtests/${r.id}`); // deletes it
  }
}

async function expertOn(page: Page): Promise<void> {
  await page.addInitScript(() => localStorage.setItem('suncore.expert', '1'));
}

/** A one-day window ending the day before the demo's (simulated) today: a short run. */
async function oneDay(page: Page): Promise<void> {
  const end = page.getByTestId('whatif-end');
  await expect(end).not.toHaveValue('');
  const value = await end.inputValue();
  await page.getByTestId('whatif-start').fill(value);
}

test.describe('What-if view', () => {
  test.beforeEach(async ({ request }) => {
    await clearRuns(request);
  });
  test.afterAll(async ({ request }) => {
    await clearRuns(request);
  });

  test('belongs to expert mode: no link and a hint while it is off', async ({ page }) => {
    await page.goto('/#/');
    await expect(page.getByRole('link', { name: 'What-if' })).toHaveCount(0);
    await page.goto('/#/whatif');
    await expect(page.getByTestId('whatif-expert-off')).toBeVisible();
    await expect(page.getByTestId('whatif-form')).toHaveCount(0);
    await page.getByRole('switch', { name: 'Expert mode' }).click();
    await expect(page.getByTestId('whatif-form')).toBeVisible();
    await expect(page.getByRole('link', { name: 'What-if' })).toBeVisible();
  });

  test('offers only the what-if parameters and the 14-day default', async ({ page }) => {
    await expertOn(page);
    await page.goto('/#/whatif');
    await expect(page.getByTestId('whatif-days')).toHaveText('14 days');
    const keys = await page
      .getByTestId('whatif-param')
      .evaluateAll((els) => els.map((e) => e.getAttribute('data-key')));
    expect(keys.sort()).toEqual(['planner.budget_s', 'planner.mip_gap']);
    await expectNoAxeViolations(page);
  });

  test('checks the form before it asks the server', async ({ page }) => {
    await expertOn(page);
    await page.goto('/#/whatif');
    await page.getByTestId('whatif-tariff-fixed').check();
    await page.getByTestId('whatif-start-run').click();
    await expect(page.getByTestId('whatif-form').getByTestId('field-issues').first()).toContainText(
      'Enter a value',
    );
    await page.getByTestId('whatif-end').fill('2999-01-01');
    await page.getByTestId('whatif-start-run').click();
    await expect(page.getByTestId('whatif-form')).toContainText('days that are over');
    expect((await page.request.get('/api/v1/backtests')).ok()).toBe(true);
    expect(await (await page.request.get('/api/v1/backtests')).json()).toEqual([]);
  });

  test('runs a replay, compares it and lists the limitations', async ({ page }) => {
    await expertOn(page);
    await page.goto('/#/whatif');
    await oneDay(page);
    await page.getByLabel('Solver optimality gap').fill('0.02');
    await page.getByLabel('Battery capacity').fill('12');
    await page.getByTestId('whatif-start-run').click();
    const result = page.getByTestId('whatif-result');
    await expect(result).toBeVisible({ timeout: 90_000 });
    await expect(page.getByTestId('whatif-verdict')).toContainText(/With these settings/);
    await expect(page.getByTestId('whatif-limitations')).toContainText('executed perfectly');
    await expect(page.getByTestId('whatif-changes')).toContainText('Solver optimality gap');
    await expect(page.getByTestId('whatif-metrics').getByRole('row')).toHaveCount(9);
    await expect(page.getByTestId('whatif-ladder')).toContainText('Without PV');
    for (const chart of ['cost', 'soc', 'grid']) {
      await expect(
        page.getByTestId(`whatif-plot-${chart}`).locator('canvas').first(),
      ).toBeVisible();
    }
    await expect(page.getByTestId('whatif-days-table').getByRole('row')).toHaveCount(2);
    const download = page.waitForEvent('download');
    await page.getByTestId('whatif-csv').click();
    expect((await download).suggestedFilename()).toMatch(/^suncore-whatif-.*\.csv$/);
    await expect(page.getByTestId('whatif-run')).toHaveCount(1);
    await expectNoAxeViolations(page);
  });

  test('cancels a long run and deletes a finished one', async ({ page, request }) => {
    await expertOn(page);
    await page.goto('/#/whatif');
    await page.getByTestId('whatif-perfect').check();
    // The server refuses (409) while the planner itself is solving: ask again until it takes it.
    await expect(async () => {
      await page.getByTestId('whatif-start-run').click();
      await expect(page.getByTestId('whatif-progress')).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 30_000 });
    await expect(page.getByTestId('whatif-start-run')).toBeDisabled();
    await page.getByTestId('whatif-cancel').click();
    await expect(page.getByTestId('whatif-progress')).toHaveCount(0);
    await expect(page.getByTestId('whatif-run').first()).toHaveAttribute(
      'data-status',
      'cancelled',
    );
    await expect(page.getByTestId('whatif-stopped')).toContainText('cancelled');
    await page.getByTestId('whatif-run-delete').click();
    await expect(page.getByTestId('whatif-runs-empty')).toBeVisible();
    expect(await (await request.get('/api/v1/backtests')).json()).toEqual([]);
  });
});

test.describe('What-if view in German', () => {
  test.use({ locale: 'de-DE' });
  test('is translated and passes the accessibility check', async ({ page }) => {
    await expertOn(page);
    await page.goto('/#/whatif');
    await expect(page.getByRole('heading', { name: 'Was wäre wenn', level: 2 })).toBeVisible();
    await expect(page.getByTestId('whatif-days')).toHaveText('14 Tage');
    await expect(page.getByTestId('whatif-start-run')).toHaveText('Backtest starten');
    await expectNoAxeViolations(page);
  });
});
