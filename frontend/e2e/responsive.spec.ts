// SPDX-License-Identifier: Apache-2.0
import { expect, test } from '@playwright/test';

test('no horizontal scroll and the diagram fits the viewport', async ({ page }) => {
  for (const hash of ['#/', '#/settings', '#/settings/system', '#/settings/ha', '#/setup/grid']) {
    await page.goto(`/${hash}`);
    await expect(page.getByTestId('connection')).toBeVisible();
    const { scroll, client } = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(scroll).toBeLessThanOrEqual(client);
  }
  await page.goto('/');
  const box = await page.getByRole('figure', { name: 'Energy flow diagram' }).boundingBox();
  const vw = page.viewportSize()!.width;
  expect(box).not.toBeNull();
  expect(box!.width).toBeLessThanOrEqual(vw);
  // Content width at most 40rem (640 px), minus the page and card gutters on a phone.
  expect(box!.width).toBeGreaterThan(Math.min(vw - 64, 640) * 0.9);
});

test('touch targets of navigation are at least 44 px', async ({ page }) => {
  await page.goto('/');
  for (const name of ['Now', 'Settings']) {
    const box = await page.getByRole('link', { name }).boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
  }
});
