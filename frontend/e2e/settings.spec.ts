// SPDX-License-Identifier: Apache-2.0
import { expect, test } from '@playwright/test';
import { resetDemo } from './helpers';

test.describe.configure({ mode: 'serial' });

test.describe('Settings overview', () => {
  test.beforeEach(async ({ page, request }) => {
    await resetDemo(request);
    await page.goto('/#/settings');
    await expect(page.getByRole('heading', { name: 'Settings', level: 2 })).toBeVisible();
    await expect(page.getByTestId('overview')).toContainText('sensor.sim_grid_power');
  });
  test.afterAll(async ({ request }) => resetDemo(request));

  test('lists all sections with edit links into single steps', async ({ page }) => {
    const items = page.getByTestId('overview').getByRole('listitem');
    await expect(items).toHaveCount(10);
    await expect(page.getByTestId('overview')).toContainText('One signed sensor');
    await expect(page.getByTestId('overview')).toContainText('1 solar array');
    await expect(page.getByTestId('overview')).toContainText('Battery: sensor.sim_battery_power');
    await expect(page.getByTestId('wizard-status')).toContainText('finished');
  });

  test('re-entry: edit one step, change a value, come back to the overview', async ({
    page,
    request,
  }) => {
    await page.getByRole('link', { name: /Edit Grid connection/ }).click();
    await expect(page).toHaveURL(/#\/setup\/grid$/);
    await expect(page.getByLabel('Grid power (positive = import)')).toHaveValue(
      'sensor.sim_grid_power',
    );
    await page.getByLabel('Connection limit for import').fill('11000');
    await page.getByTestId('wizard-next').click();
    await expect(page).toHaveURL(/#\/setup\/pv$/);
    expect((await (await request.get('/api/v1/config/site')).json()).grid.import_limit_w).toBe(
      11000,
    );
    await page.getByRole('button', { name: 'Leave setup' }).click();
    await expect(page).toHaveURL(/#\/settings$/);
  });

  test('history: restore the previous version of a section', async ({ page, request }) => {
    await request.put('/api/v1/config/goals', {
      data: { schema_version: 1, climate_before_export: true, grid_charge_night_only: true },
    });
    await page.reload();
    await page.getByLabel('Section').selectOption({ label: 'Goals' });
    await page.getByRole('button', { name: 'Show versions' }).click();
    const history = page.getByTestId('history');
    await expect(history.getByRole('listitem').first()).toBeVisible();
    await history
      .getByRole('button', { name: /^Restore/ })
      .first()
      .click();
    await history.getByRole('button', { name: 'Really restore' }).click();
    await expect(page.getByText('The earlier version was restored.')).toBeVisible();
    expect((await (await request.get('/api/v1/config/goals')).json()).climate_before_export).toBe(
      false,
    );
  });

  test('export and import round trip', async ({ page, request }) => {
    const href = await page.getByTestId('export').getAttribute('href');
    expect(href).toMatch(/api\/v1\/config\/export$/);
    const exported = await (await request.get('/api/v1/config/export')).text();
    await request.put('/api/v1/config/goals', {
      data: { schema_version: 1, climate_before_export: true, grid_charge_night_only: false },
    });
    await page.getByTestId('import-file').setInputFiles({
      name: 'suncore-config.json',
      mimeType: 'application/json',
      buffer: Buffer.from(exported),
    });
    await expect(page.getByTestId('import-result')).toContainText('Imported');
    expect((await (await request.get('/api/v1/config/goals')).json()).climate_before_export).toBe(
      false,
    );
  });

  test('a rejected import file is reported', async ({ page }) => {
    await page.getByTestId('import-file').setInputFiles({
      name: 'bad.json',
      mimeType: 'application/json',
      buffer: Buffer.from('not json'),
    });
    await expect(page.getByTestId('import-result')).toContainText('not valid JSON');
  });
});
