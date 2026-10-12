// SPDX-License-Identifier: Apache-2.0
import { expect, test } from '@playwright/test';

test.describe('Settings', () => {
  test.beforeEach(async ({ page }) => {
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

  test('picks entities from the candidates (datalist) and adds a second PV sensor', async ({
    page,
    request,
  }) => {
    const options = await page
      .locator('datalist option')
      .evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value));
    expect(options).toEqual(
      expect.arrayContaining([
        'sensor.sim_grid_power',
        'sensor.sim_pv_power',
        'sensor.sim_battery_power',
        'sensor.sim_battery_soc',
        'sensor.sim_house_power',
      ]),
    );

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
});
