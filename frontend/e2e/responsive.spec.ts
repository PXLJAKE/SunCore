// SPDX-License-Identifier: Apache-2.0
import { expect, test } from '@playwright/test';

test('no horizontal scroll and the diagram fits the viewport', async ({ page }) => {
  for (const hash of ['#/', '#/settings']) {
    await page.goto(`/${hash}`);
    await expect(page.getByTestId('connection')).toBeVisible();
    const { scroll, client } = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(scroll).toBeLessThanOrEqual(client);
  }
  await page.goto('/');
  const box = await page.locator('figure svg').boundingBox();
  const vw = page.viewportSize()!.width;
  expect(box).not.toBeNull();
  expect(box!.width).toBeLessThanOrEqual(vw);
  expect(box!.width).toBeGreaterThan(Math.min(vw, 760) * 0.8);
});

test('touch targets of navigation are at least 44 px', async ({ page }) => {
  await page.goto('/');
  for (const name of ['Now', 'Settings']) {
    const box = await page.getByRole('link', { name }).boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
  }
});
