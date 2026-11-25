// SPDX-License-Identifier: Apache-2.0
import { expect, test, type Page } from '@playwright/test';
import { expectNoAxeViolations, openCard, resetDemo } from './helpers';

const NAMES: Record<string, string> = {
  living_room_ac: 'Living room AC',
  pool_pump: 'Pool pump',
  heating_rod: 'Heating rod',
};

interface Row {
  intent: { asset_id: string };
}

/** The controller logs a shadow decision for each device within a few ticks of the demo start. */
async function firstRow(page: Page): Promise<void> {
  await expect(page.getByTestId('decision-table').locator('tbody tr').first()).toBeVisible({
    timeout: 40_000,
  });
}

test.describe('decision log', () => {
  test.afterEach(async ({ request }) => resetDemo(request));

  test('lists decisions with rendered reasons; no axe violations', async ({ page }) => {
    await page.goto('/#/decisions');
    await expect(page.getByRole('heading', { name: 'Decision log', level: 2 })).toBeVisible();
    await firstRow(page);
    const list = page.getByTestId('log-list');
    await expect(list).not.toContainText('safety.shadow_mode');
    await expect(list).toContainText('Would have switched');
    await expect(page.getByTestId('log-count')).toContainText('of');
    await expectNoAxeViolations(page);
  });

  test('filters by device, outcome, layer, reason and text and keeps them in the URL', async ({
    page,
    request,
  }) => {
    await page.goto('/#/decisions');
    await firstRow(page);
    // On a phone the filters start collapsed.
    await openCard(page, 'log-filter-card');
    const all = (await (await request.get('/api/v1/decisions?limit=200')).json()) as Row[];
    // a device with a card (the log also holds the battery's and the inverter's decisions)
    const id = all.map((d) => d.intent.asset_id).find((a) => a in NAMES)!;

    await page.getByTestId('filter-device').selectOption(id);
    await expect(page).toHaveURL(new RegExp(`#/decisions\\?device=${id}$`));
    const rows = page.getByTestId('decision-table').locator('tbody tr');
    await expect(rows.first()).toBeVisible();
    for (const text of await rows.allTextContents()) expect(text).toContain(NAMES[id] ?? id);

    // an outcome nothing has happened for gives the filtered empty state
    await page.getByTestId('filter-device').selectOption('');
    await page.getByTestId('filter-kind-failed').check();
    await expect(page.getByTestId('log-list')).toContainText('No decisions match these filters');
    await page.getByTestId('filter-kind-failed').uncheck();
    await page.getByTestId('filter-kind-shadow').check();
    await expect(rows.first()).toContainText('Would have switched');

    // layer: the demo's decisions come from the planner or the controller, none from the user
    await page.getByTestId('filter-layer').selectOption('user');
    await expect(page.getByTestId('log-list')).toContainText('No decisions match these filters');
    await page.getByTestId('filter-reset').click();
    await expect(page).toHaveURL(/#\/decisions$/);

    // reason prefix and free text
    await page.getByTestId('filter-reason').fill('safety');
    await expect(page).toHaveURL(/reason=safety$/);
    await expect(rows.first()).toBeVisible();
    await page.getByTestId('filter-q').fill('no-such-device-xyz');
    await expect(page.getByTestId('log-list')).toContainText('No decisions match these filters');
    await expectNoAxeViolations(page);
  });

  test('restores the filters from a shared link', async ({ page, request }) => {
    const all = (await (await request.get('/api/v1/decisions?limit=200')).json()) as Row[];
    const id = all[0]?.intent.asset_id ?? 'living_room_ac';
    await page.goto(`/#/decisions?device=${id}&kind=shadow&from=2020-01-01`);
    await expect(page.getByTestId('filter-device')).toHaveValue(id);
    await expect(page.getByTestId('filter-kind-shadow')).toBeChecked();
    await expect(page.getByTestId('filter-from')).toHaveValue('2020-01-01');
    // a link with junk keeps the valid parts only
    await page.goto('/#/decisions?layer=nonsense&kind=bogus');
    await expect(page.getByTestId('filter-layer')).toHaveValue('');
    await expect(page.getByTestId('filter-kind-shadow')).not.toBeChecked();
  });

  test('rejects a reversed date range before asking the server', async ({ page }) => {
    await page.goto('/#/decisions');
    await openCard(page, 'log-filter-card');
    await page.getByTestId('filter-from').fill('2026-06-10');
    await page.getByTestId('filter-to').fill('2026-06-01');
    await expect(
      page.getByRole('alert').filter({ hasText: 'before the start date' }),
    ).toBeVisible();
    await expect(page).not.toHaveURL(/to=/);
  });

  test('exports the filtered log as CSV', async ({ page, request }) => {
    await page.goto('/#/decisions?kind=shadow');
    await firstRow(page);
    const href = (await page.getByTestId('log-export').getAttribute('href'))!;
    expect(href).toContain('format=csv');
    expect(href).toContain('kind=shadow');
    const res = await request.get(href);
    expect(res.ok()).toBe(true);
    expect(res.headers()['content-type']).toContain('text/csv');
    const text = await res.text();
    expect(text.split('\n')[0]).toContain('device_id');
    expect(text).toContain('shadow');
  });

  test('"Why?" explains a decision, by keyboard, and links to plan and device', async ({
    page,
  }) => {
    await page.goto('/#/decisions?device=pool_pump');
    await firstRow(page);
    const button = page.getByTestId('why-button').first();
    await button.focus();
    await page.keyboard.press('Enter');
    await expect(button).toHaveAttribute('aria-expanded', 'true');
    const panel = page.getByTestId('why-panel');
    await expect(panel).toBeVisible();
    await expect(panel.getByRole('heading', { name: 'Why this decision' })).toBeVisible();
    await expect(panel).toContainText('Reasons');
    await expect(panel).toContainText('The plan at that time');
    await expect(panel).toContainText('Readings at that moment');
    await expect(panel).not.toContainText('safety.shadow_mode');
    await expectNoAxeViolations(page);

    // Escape closes the panel and returns the focus to its button
    await panel.focus();
    await page.keyboard.press('Escape');
    await expect(panel).toHaveCount(0);
    await expect(button).toBeFocused();
    await expect(button).toHaveAttribute('aria-expanded', 'false');

    // the link to the device card lands on that card
    await button.click();
    await page.getByTestId('why-device-link').click();
    await expect(page).toHaveURL(/#\/\?device=/);
    await expect(page.locator('article[id^="device-card-"]:focus')).toBeVisible();
  });

  test('works on a phone width without horizontal scrolling', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/#/decisions');
    await firstRow(page);
    await page.getByTestId('why-button').first().click();
    await expect(page.getByTestId('why-panel')).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    await expectNoAxeViolations(page);
  });
});
