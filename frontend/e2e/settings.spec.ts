// SPDX-License-Identifier: Apache-2.0
import { expect, test } from '@playwright/test';
import { resetDemo } from './helpers';

test.describe.configure({ mode: 'serial' });

test.describe('Settings overview', () => {
  test.beforeEach(async ({ page, request }) => {
    await resetDemo(request);
    await page.goto('/#/settings/system');
    await expect(page.getByRole('heading', { name: 'Settings', level: 2 })).toBeVisible();
    await expect(page.getByTestId('overview')).toContainText('sensor.sim_grid_power');
  });
  test.afterAll(async ({ request }) => resetDemo(request));

  test('lists all sections with edit links into single steps', async ({ page }) => {
    // 7-G: the ten steps are spread over three sub-pages (6 + 1 + 3).
    const items = page.getByTestId('overview').getByRole('listitem');
    await expect(items).toHaveCount(6);
    await expect(page.getByTestId('overview')).toContainText('One signed sensor');
    await expect(page.getByTestId('overview')).toContainText('1 solar array');
    await expect(page.getByTestId('overview')).toContainText('Battery: sensor.sim_battery_power');
    await expect(page.getByTestId('wizard-status')).toContainText('The setup is finished');
    await page.goto('/#/settings/devices');
    await expect(items).toHaveCount(1);
    await expect(page.getByTestId('overview')).toContainText('6 devices');
    await expect(page.getByRole('link', { name: /Edit Devices/ })).toHaveAttribute(
      'href',
      '#/setup/devices',
    );
    await page.goto('/#/settings/goals');
    await expect(items).toHaveCount(3);
    await expect(page.getByRole('link', { name: /Open Summary and check/ })).toHaveAttribute(
      'href',
      '#/setup/summary',
    );
  });

  test('the index leads to every group and back', async ({ page }) => {
    await page.goto('/#/settings');
    const index = page.getByTestId('settings-index');
    await expect(index.getByRole('link')).toHaveCount(5);
    await index.getByRole('link', { name: /Home Assistant/ }).click();
    await expect(page).toHaveURL(/#\/settings\/ha$/);
    await expect(page.getByTestId('settings-page')).toHaveAttribute('data-page', 'ha');
    await expect(
      page.getByRole('heading', { name: 'Home Assistant', level: 3, exact: true }),
    ).toBeFocused();
    await expect(page.getByTestId('ha-entities')).toBeVisible();
    await expect(page.getByTestId('notifications')).toBeVisible();
    // Phones and tablets show the page alone, with the way back in the title.
    const back = page.getByTestId('settings-back');
    if (await back.isVisible()) {
      await back.click();
      await expect(page).toHaveURL(/#\/settings$/);
      await expect(page.getByTestId('settings-page')).toHaveCount(0);
    } else {
      await expect(index.getByRole('link', { name: /Home Assistant/ })).toHaveAttribute(
        'aria-current',
        'page',
      );
    }
  });

  test('about shows the running version', async ({ page, request }) => {
    const info = await (await request.get('/api/v1/info')).json();
    await page.goto('/#/settings/advanced');
    await expect(page.getByTestId('about-version')).toHaveText(info.version);
    await expect(page.getByTestId('about')).toContainText('Apache License 2.0');
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
    // the demo has `climate_before_export: true`; the replaced version is the one to get back
    await request.put('/api/v1/config/goals', {
      data: { schema_version: 2, climate_before_export: false },
    });
    await page.goto('/#/settings/advanced');
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
      true,
    );
  });

  test('export and import round trip', async ({ page, request }) => {
    await page.goto('/#/settings/advanced');
    const href = await page.getByTestId('export').getAttribute('href');
    expect(href).toMatch(/api\/v1\/config\/export$/);
    const exported = await (await request.get('/api/v1/config/export')).text();
    await request.put('/api/v1/config/goals', {
      data: { schema_version: 2, climate_before_export: false },
    });
    await page.getByTestId('import-file').setInputFiles({
      name: 'suncore-config.json',
      mimeType: 'application/json',
      buffer: Buffer.from(exported),
    });
    await expect(page.getByTestId('import-result')).toContainText('Imported');
    expect((await (await request.get('/api/v1/config/goals')).json()).climate_before_export).toBe(
      true,
    );
  });

  test('a rejected import file is reported', async ({ page }) => {
    await page.goto('/#/settings/advanced');
    await page.getByTestId('import-file').setInputFiles({
      name: 'bad.json',
      mimeType: 'application/json',
      buffer: Buffer.from('not json'),
    });
    await expect(page.getByTestId('import-result')).toContainText('not valid JSON');
  });
});
