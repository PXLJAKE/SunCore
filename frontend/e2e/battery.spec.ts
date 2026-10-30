// SPDX-License-Identifier: Apache-2.0
import { expect, test } from '@playwright/test';
import { expectNoAxeViolations, resetDemo } from './helpers';

test.describe.configure({ mode: 'serial' });

test.afterAll(async ({ request }) => {
  await resetDemo(request);
});

test.describe('battery card on the Now view', () => {
  test.beforeEach(async ({ request }) => {
    await resetDemo(request);
  });

  test('shows state, limits and the next planned actions', async ({ page }) => {
    await page.goto('/');
    const card = page.getByTestId('battery-card');
    await expect(card).toBeVisible();
    await expect(card.getByTestId('battery-effective-mode')).toHaveText('Shadow mode');
    await expect(card.getByTestId('battery-soc')).toContainText('%');
    await expect(card.getByTestId('battery-limits')).toContainText('Minimum state of charge');
    await expect(card.getByTestId('battery-control')).toContainText('own settings');
    const next = page.getByTestId('now-next-actions');
    await expect(next.getByTestId('planned-action').first()).toBeVisible();
    await next.getByText('Why?').first().click();
    await expect(next.getByRole('list').last()).toBeVisible();
    await expectNoAxeViolations(page);
  });

  test('hides the planned actions and says why when the plan is not followed', async ({ page }) => {
    await page.route('**/api/v1/plan?*', async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      await route.fulfill({ response: res, json: { ...body, stale: true, in_use: false } });
    });
    await page.goto('/');
    await expect(page.getByTestId('now-plan-outdated')).toBeVisible();
    await expect(page.getByTestId('now-planned-actions')).toHaveCount(0);
  });

  test('switches to assisted after a confirmation with the battery identity', async ({
    page,
    request,
  }) => {
    await page.goto('/');
    const card = page.getByTestId('battery-card');
    await card.getByTestId('battery-mode-assisted').click();
    const dialog = page.getByTestId('battery-dialog');
    await expect(dialog).toBeVisible();
    await expectNoAxeViolations(page);
    // Cancel changes nothing.
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(card.getByTestId('battery-mode-shadow')).toHaveAttribute('aria-pressed', 'true');
    await card.getByTestId('battery-mode-assisted').click();
    await dialog.getByRole('button', { name: 'Switch to assisted' }).click();
    await expect(card.getByTestId('battery-mode-assisted')).toHaveAttribute('aria-pressed', 'true');
    // The overall mode is shadow and caps the battery's own mode.
    await expect(card.getByTestId('battery-effective-mode')).toHaveText('Shadow mode');
    await expect(card.getByTestId('battery-mode-limited')).toBeVisible();
    const op = await (await request.get('/api/v1/config/operation')).json();
    expect(op.battery_mode).toBe('assisted');
    // Back to shadow needs no confirmation.
    await card.getByTestId('battery-mode-shadow').click();
    await expect(card.getByTestId('battery-mode-shadow')).toHaveAttribute('aria-pressed', 'true');
  });

  test('the server refuses a mode above shadow without or with a wrong identity', async ({
    request,
  }) => {
    const none = await request.put('/api/v1/operation/battery', { data: { mode: 'assisted' } });
    expect(none.ok()).toBe(false);
    const wrong = await request.put('/api/v1/operation/battery', {
      data: { mode: 'assisted', identity: 'not-the-token' },
    });
    expect(wrong.status()).toBe(409);
  });
});
