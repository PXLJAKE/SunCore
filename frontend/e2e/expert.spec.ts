// SPDX-License-Identifier: Apache-2.0
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { resetDemo } from './helpers';

test.describe.configure({ mode: 'serial' });

const row = (page: Page, key: string) => page.locator(`[data-testid="param"][data-key="${key}"]`);

async function resetParams(request: import('@playwright/test').APIRequestContext) {
  for (const group of ['controller', 'planner']) {
    const res = await request.post('/api/v1/params/reset', { data: { group } });
    expect(res.ok()).toBe(true);
  }
}

test.describe('Expert mode', () => {
  test.beforeEach(async ({ page, request }) => {
    await resetDemo(request);
    await resetParams(request);
    await page.goto('/#/settings');
    await expect(page.getByRole('heading', { name: 'Settings', level: 2 })).toBeVisible();
  });
  test.afterAll(async ({ request }) => {
    await resetParams(request);
    await resetDemo(request);
  });

  test('is off by default, the header switch reveals the area and is remembered', async ({
    page,
  }) => {
    await expect(page.getByTestId('expert-area')).toHaveCount(0);
    await expect(page.getByTestId('expert-off')).toBeVisible();
    const toggle = page.getByRole('switch', { name: 'Expert mode' });
    await expect(toggle).toHaveAttribute('aria-checked', 'false');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByRole('heading', { name: 'Expert settings' })).toBeVisible();
    await expect(row(page, 'controller.smoothing_s')).toBeVisible();
    await page.reload();
    await expect(page.getByRole('switch', { name: 'Expert mode' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    await expect(page.getByTestId('expert-area')).toBeVisible();
  });

  test('changes a non-safety value without confirmation, then resets it', async ({
    page,
    request,
  }) => {
    await page.getByRole('switch', { name: 'Expert mode' }).click();
    const field = row(page, 'controller.smoothing_s');
    await field.getByRole('spinbutton').fill('90');
    await page.getByTestId('param-save').click();
    await expect(page.getByTestId('param-notice')).toContainText('1 parameter saved');
    await expect(field.getByTestId('modified-marker')).toBeVisible();
    const listing = await (await request.get('/api/v1/params')).json();
    expect(
      listing.params.find((p: { key: string }) => p.key === 'controller.smoothing_s').value,
    ).toBe(90);

    await field.getByTestId('param-reset').click();
    await expect(field.getByTestId('modified-marker')).toHaveCount(0);
    await expect(field.getByRole('spinbutton')).toHaveValue('60');
  });

  test('a safety value needs the confirmation dialog', async ({ page, request }) => {
    await page.getByRole('switch', { name: 'Expert mode' }).click();
    const field = row(page, 'controller.reserve_w');
    await field.getByRole('spinbutton').fill('150');
    await page.getByTestId('param-save').click();
    const dialog = page.getByTestId('safety-confirm');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByTestId('safety-list')).toContainText('from 100 to 150');
    // Not stored before the confirmation.
    let listing = await (await request.get('/api/v1/params')).json();
    expect(
      listing.params.find((p: { key: string }) => p.key === 'controller.reserve_w').value,
    ).toBe(100);
    await dialog.getByTestId('safety-confirm-confirm').click();
    await expect(field.getByTestId('modified-marker')).toBeVisible();
    listing = await (await request.get('/api/v1/params')).json();
    expect(
      listing.params.find((p: { key: string }) => p.key === 'controller.reserve_w').value,
    ).toBe(150);
  });

  test('shows the server error for a value outside the hard limit', async ({ page }) => {
    await page.getByRole('switch', { name: 'Expert mode' }).click();
    const field = row(page, 'controller.smoothing_s');
    // The browser input has no client limit when it bypasses `max`: the form blocks it first.
    await field.getByRole('spinbutton').fill('99999999');
    await expect(field.getByTestId('field-issues')).toBeVisible();
    await expect(page.getByTestId('param-save')).toBeDisabled();
  });

  test('group reset and debug views', async ({ page }) => {
    await page.getByRole('switch', { name: 'Expert mode' }).click();
    await row(page, 'controller.smoothing_s').getByRole('spinbutton').fill('90');
    await page.getByTestId('param-save').click();
    await expect(page.getByTestId('param-notice')).toBeVisible();
    await page.locator('[data-group="controller"]').getByTestId('group-reset').click();
    await expect(row(page, 'controller.smoothing_s').getByTestId('modified-marker')).toHaveCount(0);

    await page.getByTestId('param-search').fill('smoothing');
    await expect(page.getByTestId('param')).toHaveCount(1);

    await page.getByText('Raw signals').click();
    await expect(page.getByTestId('signals-json')).toContainText('flows');
    await page.getByTestId('debug-load').click();
    await expect(page.getByTestId('plan-json')).toBeVisible();
    await expect(page.getByTestId('solver-stats')).toBeVisible();
  });

  for (const scheme of ['light', 'dark'] as const) {
    test(`has no axe violations (${scheme})`, async ({ browser }) => {
      const ctx = await browser.newContext({ colorScheme: scheme });
      const page = await ctx.newPage();
      await page.goto('/#/settings');
      await page.getByRole('switch', { name: 'Expert mode' }).click();
      await expect(row(page, 'controller.smoothing_s')).toBeVisible();
      await page.getByTestId('debug-load').click();
      await expect(page.getByTestId('plan-json')).toBeVisible();
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze();
      expect(results.violations.map((v) => `${v.id}: ${v.nodes[0]?.target}`)).toEqual([]);
      await ctx.close();
    });
  }
});
