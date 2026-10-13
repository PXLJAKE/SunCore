// SPDX-License-Identifier: Apache-2.0
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const bg = (page: import('@playwright/test').Page) =>
  page.evaluate(() => getComputedStyle(document.body).backgroundColor);

test.describe('theme', () => {
  test('follows prefers-color-scheme', async ({ browser }) => {
    const light = await browser.newContext({ colorScheme: 'light' });
    const lp = await light.newPage();
    await lp.goto('/');
    const lightBg = await bg(lp);
    await light.close();

    const dark = await browser.newContext({ colorScheme: 'dark' });
    const dp = await dark.newPage();
    await dp.goto('/');
    const darkBg = await bg(dp);
    await dark.close();

    expect(lightBg).not.toBe(darkBg);
  });

  test('?theme= overrides the system scheme', async ({ browser }) => {
    const ctx = await browser.newContext({ colorScheme: 'light' });
    const page = await ctx.newPage();
    await page.goto('/?theme=dark');
    const scheme = await page.evaluate(
      () => getComputedStyle(document.documentElement).colorScheme,
    );
    expect(scheme).toBe('dark');
    const light = await ctx.newPage();
    await light.goto('/?theme=light');
    expect(await light.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe(
      'light',
    );
    expect(await bg(light)).not.toBe(await bg(page));
    await ctx.close();
  });
});

for (const scheme of ['light', 'dark'] as const) {
  test.describe(`a11y (${scheme})`, () => {
    test.use({ colorScheme: scheme });

    for (const [name, hash] of [
      ['now', '#/'],
      ['settings', '#/settings'],
    ] as const) {
      test(`${name} view has no axe violations`, async ({ page }) => {
        await page.goto(`/${hash}`);
        await expect(page.getByTestId('connection')).toHaveAttribute('data-status', 'live');
        if (name === 'settings') {
          await expect(page.getByTestId('overview')).toContainText('sim_grid_power');
        }
        const results = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
          .analyze();
        expect(results.violations.map((v) => `${v.id}: ${v.nodes[0]?.target}`)).toEqual([]);
      });
    }
  });
}

test('keyboard: skip link and navigation are reachable', async ({ page }) => {
  await page.goto('/');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Skip to content' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Now' })).toBeFocused();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#\/settings$/);
  await expect(page.getByRole('heading', { name: 'Settings', level: 2 })).toBeVisible();
});

test('diagram has a text alternative listing every flow', async ({ page }) => {
  await page.goto('/');
  const figure = page.getByRole('figure', { name: 'Energy flow diagram' });
  await expect(figure).toBeVisible();
  await expect(figure.locator('svg')).toHaveAttribute('aria-hidden', 'true');
  const items = page.getByRole('list', { name: 'Current power values' }).getByRole('listitem');
  await expect(items).toHaveCount(4);
});
