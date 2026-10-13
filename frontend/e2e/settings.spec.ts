// SPDX-License-Identifier: Apache-2.0
import { expect, test } from '@playwright/test';

const ref = (entity_id: string) => ({ entity_id, invert_sign: false });
const DEMO_SITE = {
  schema_version: 1,
  grid: { power: ref('sensor.sim_grid_power'), import_power: null, export_power: null },
  pv: [ref('sensor.sim_pv_power')],
  battery: { power: ref('sensor.sim_battery_power'), soc: ref('sensor.sim_battery_soc') },
  house: ref('sensor.sim_house_power'),
};

test.describe('Settings', () => {
  test.beforeEach(async ({ page, request }) => {
    // Never inherit state from a previous test, retry or project.
    const res = await request.put('/api/v1/config/site', { data: DEMO_SITE });
    expect(res.ok()).toBe(true);
    await page.goto('/#/settings');
    await expect(page.getByRole('heading', { name: 'Settings', level: 2 })).toBeVisible();
    await expect(page.getByLabel('Grid power (positive = import)')).toHaveValue(
      'sensor.sim_grid_power',
    );
  });

  test('save round trip: change, save, reload shows the change', async ({ page, request }) => {
    const grid = page.getByRole('group', { name: 'Grid connection' });
    const invert = grid.getByLabel('Invert sign');
    await expect(invert).not.toBeChecked();
    await expect(grid.getByTestId('preview')).toContainText('Sensor reads');

    await invert.check();
    await expect(grid.getByTestId('preview')).toContainText('Sensor reads');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByTestId('saved')).toBeVisible();

    const stored = await (await request.get('/api/v1/config/site')).json();
    expect(stored.grid.power).toEqual({ entity_id: 'sensor.sim_grid_power', invert_sign: true });

    await page.reload();
    await expect(
      page.getByRole('group', { name: 'Grid connection' }).getByLabel('Invert sign'),
    ).toBeChecked();

    // restore the demo configuration
    await page.getByRole('group', { name: 'Grid connection' }).getByLabel('Invert sign').uncheck();
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByTestId('saved')).toBeVisible();
  });

  test('picks entities from the candidates and adds a second PV sensor', async ({
    page,
    request,
  }) => {
    await page.getByLabel('Solar power 1').focus();
    const options = page.getByRole('option');
    await expect(options.filter({ hasText: 'sensor.sim_pv_power' })).toBeVisible();
    await expect(options.filter({ hasText: 'sensor.sim_battery_soc' })).toHaveCount(0);
    await page.keyboard.press('Escape');

    // Every sim entity is in use; free the house sensor first (entities must be unique).
    await page.getByLabel('House consumption').fill('');
    await page.getByRole('button', { name: 'Add solar sensor' }).click();
    await page.getByLabel('Solar power 2').fill('sensor.sim_house_power');
    await expect(page.getByText('2 solar sensors')).toBeVisible();
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByTestId('saved')).toBeVisible();
    const stored = await (await request.get('/api/v1/config/site')).json();
    expect(stored.pv.map((p: { entity_id: string }) => p.entity_id)).toEqual([
      'sensor.sim_pv_power',
      'sensor.sim_house_power',
    ]);

    // restore
    await page.getByRole('button', { name: 'Remove' }).last().click();
    await page.getByLabel('House consumption').fill('sensor.sim_house_power');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByTestId('saved')).toBeVisible();
    const restored = await (await request.get('/api/v1/config/site')).json();
    expect(restored.pv).toHaveLength(1);
    expect(restored.house.entity_id).toBe('sensor.sim_house_power');
  });

  test('blocks saving an incomplete battery or grid pair on the client', async ({ page }) => {
    let puts = 0;
    page.on('request', (r) => {
      if (r.method() === 'PUT') puts += 1;
    });
    await page.getByLabel('Battery state of charge (%)').fill('');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByTestId('form-error')).toContainText('Select both the battery power');
    await page.getByLabel('Battery state of charge (%)').fill('sensor.sim_battery_soc');
    await page.getByLabel('Separate import and export sensors').check();
    await page.getByLabel('Import power (grid consumption)').fill('sensor.sim_grid_power');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByTestId('form-error')).toContainText('Select both the import');
    expect(puts).toBe(0);
  });

  test('shows validation errors from the API', async ({ page }) => {
    await page.getByLabel('Grid power (positive = import)').fill('not an entity');
    await page.getByRole('button', { name: 'Save' }).click();
    const err = page.getByTestId('save-error');
    await expect(err).toBeVisible();
    await expect(err).toContainText('Some values are invalid');
    await expect(err.getByRole('listitem').first()).toBeVisible();
    await expect(page.getByTestId('saved')).toHaveCount(0);
  });

  test('renders api.error keys through the catalogue', async ({ page }) => {
    await page.route('**/api/v1/config/site', async (route) => {
      if (route.request().method() === 'PUT') {
        await route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'api.error.ha_unavailable' }),
        });
      } else {
        await route.continue();
      }
    });
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByTestId('save-error')).toContainText(
      'Home Assistant is not reachable right now.',
    );
  });

  test.describe('entity combobox', () => {
    test('finds the SoC sensor by friendly name: keyboard and click', async ({ page }) => {
      const soc = page.getByLabel('Battery state of charge (%)');
      const socPreview = page.locator('.field', { has: soc }).getByTestId('preview');

      await soc.fill('');
      await soc.fill('battery soc');
      await expect(soc).toHaveAttribute('aria-expanded', 'true');
      const option = page.getByRole('option', { name: /Sim battery SoC/ });
      await expect(option).toHaveCount(1);
      await expect(option).toContainText('sensor.sim_battery_soc');
      await expect(option).toContainText('%');
      expect((await option.boundingBox())!.height).toBeGreaterThanOrEqual(44);

      // keyboard
      await soc.press('ArrowDown');
      await expect(soc).toHaveAttribute('aria-activedescendant', /-opt-0$/);
      await soc.press('Enter');
      await expect(soc).toHaveValue('sensor.sim_battery_soc');
      await expect(soc).toHaveAttribute('aria-expanded', 'false');
      await expect(socPreview).toContainText(/Sensor reads \d+(\.\d+)?\s?%/);

      // click
      await soc.fill('');
      await soc.fill('sim battery');
      await page.getByRole('option', { name: /Sim battery SoC/ }).click();
      await expect(soc).toHaveValue('sensor.sim_battery_soc');
      await expect(socPreview).toContainText('%');
    });

    test('an exact friendly name resolves, unknown text shows a hint', async ({ page }) => {
      const soc = page.getByLabel('Battery state of charge (%)');
      const socPreview = page.locator('.field', { has: soc }).getByTestId('preview');
      await soc.fill('sim battery soc');
      await expect(socPreview).toContainText('%');
      await soc.blur();
      await expect(soc).toHaveValue('sensor.sim_battery_soc');
      await soc.fill('does not exist');
      await expect(socPreview).toContainText('Not found among the Home Assistant battery sensors');
      await expect(page.getByText('No matching sensors found')).toBeVisible();
      await soc.press('Escape');
      await expect(soc).toHaveAttribute('aria-expanded', 'false');
    });

    test('power fields never offer the SoC sensor; focus lists all of the kind', async ({
      page,
    }) => {
      const grid = page.getByLabel('Grid power (positive = import)');
      await grid.focus();
      await expect(grid).toHaveAttribute('aria-expanded', 'true');
      await expect(page.getByRole('option', { name: /Sim grid power/ })).toBeVisible();
      await expect(page.getByRole('option', { name: /Sim house power/ })).toBeVisible();
      await expect(page.getByRole('option', { name: /Sim battery SoC/ })).toHaveCount(0);
      await grid.fill('soc');
      await expect(page.getByRole('option')).toHaveCount(0);
      // outside click closes
      await page.getByRole('heading', { name: 'Settings', level: 2 }).click();
      await expect(grid).toHaveAttribute('aria-expanded', 'false');
    });
  });
});
