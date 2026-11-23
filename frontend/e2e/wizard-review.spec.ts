// SPDX-License-Identifier: Apache-2.0
import { expect, test } from '@playwright/test';
import { expectNoAxeViolations, openStep, resetDemo } from './helpers';

const next = (page: import('@playwright/test').Page) => page.getByTestId('wizard-next');

test.describe.configure({ mode: 'serial' });

test.afterAll(async ({ request }) => {
  await resetDemo(request);
});

test.describe('dynamic tariff with only a price sensor', () => {
  test.beforeEach(async ({ request }) => {
    await resetDemo(request, { wizardDone: false });
  });

  test('offers the price sensors, stores the entity without a template', async ({
    page,
    request,
  }) => {
    await openStep(page, 'tariff');
    const imp = page.getByRole('group', { name: 'Import price' });
    await imp.getByRole('radio', { name: 'Dynamic price', exact: true }).check();
    await imp.getByRole('radio', { name: 'Only a price sensor' }).check();
    const picker = imp.getByRole('combobox', { name: 'Price sensor' });
    await picker.focus();
    const option = page.getByRole('option', { name: /Sim electricity price/ });
    await expect(option).toBeVisible();
    await expect(option).toContainText('sensor.sim_electricity_price');
    await expect(option).toContainText('EUR/kWh');
    // Only price sensors: power and battery sensors are not offered.
    await expect(page.getByRole('option', { name: /Sim grid power/ })).toHaveCount(0);
    await option.click();
    await expect(picker).toHaveValue('sensor.sim_electricity_price');
    await page
      .getByRole('group', { name: 'Feed-in remuneration' })
      .getByLabel(/^Price per kWh/)
      .fill('0.07');
    await expectNoAxeViolations(page);
    await next(page).click();
    await expect(page).toHaveURL(/#\/setup\/devices$/);

    const tariff = await (await request.get('/api/v1/config/tariff')).json();
    expect(tariff.import).toMatchObject({
      kind: 'dynamic',
      template_id: null,
      price_entity: { entity_id: 'sensor.sim_electricity_price' },
    });
    expect(tariff.import.fixed_price).toBeNull();
    expect(tariff.schema_version).toBe(3);
  });

  test('the forecast choice is stored without a complete tariff', async ({ page, request }) => {
    await request.put('/api/v1/config/forecast', {
      data: { schema_version: 2, sources: [] },
    });
    await openStep(page, 'pv');
    await page.getByLabel('Solcast PV forecast').check();
    const today = page.getByLabel(/^Solcast sensor for today/).first();
    await today.fill('sensor.sim_solcast_forecast_today');
    await page.keyboard.press('Escape');
    await next(page).click();
    await expect(page).toHaveURL(/#\/setup\/battery$/);
    const forecast = await (await request.get('/api/v1/config/forecast')).json();
    expect(forecast.sources.map((s: { template_id: string }) => s.template_id)).toEqual([
      'solcast.pv',
    ]);
    expect(forecast.sources[0].entities.forecast_today.entity_id).toBe(
      'sensor.sim_solcast_forecast_today',
    );
  });
});

test.describe('finishing, leaving and running the setup again', () => {
  test.beforeEach(async ({ request }) => {
    await resetDemo(request, { wizardDone: false });
  });

  test('finish is disabled while the check reports an error, and says why', async ({
    page,
    request,
  }) => {
    const site = await (await request.get('/api/v1/config/site')).json();
    site.house = { entity_id: 'sensor.does_not_exist', invert_sign: false };
    expect((await request.put('/api/v1/config/site', { data: site })).ok()).toBe(true);
    await openStep(page, 'summary');
    await expect(page.getByTestId('check-overall')).toHaveAttribute('data-severity', 'error');
    await expect(next(page)).toBeDisabled();
    await expect(page.getByTestId('blocked-hint')).toContainText('cannot be finished yet');
    await expectNoAxeViolations(page);
    // fixed: the button is enabled again after a new check
    site.house = { entity_id: 'sensor.sim_house_power', invert_sign: false };
    await request.put('/api/v1/config/site', { data: site });
    await page.getByRole('button', { name: 'Check again' }).click();
    await expect(next(page)).toBeEnabled();
  });

  test('leaving with unsaved edits asks first; keep editing stays, discard drops them', async ({
    page,
  }) => {
    await openStep(page, 'region');
    await page.getByLabel('Currency (ISO code)').fill('CHF');
    await page.getByRole('button', { name: 'Leave setup' }).click();
    const confirm = page.getByTestId('leave-confirm');
    await expect(confirm).toBeVisible();
    await expectNoAxeViolations(page);
    await page.getByTestId('leave-stay').click();
    await expect(confirm).toBeHidden();
    await expect(page).toHaveURL(/#\/setup\/region$/);
    await expect(page.getByLabel('Currency (ISO code)')).toHaveValue('CHF');

    await page.getByRole('button', { name: 'Leave setup' }).click();
    await page.getByTestId('leave-discard').click();
    await expect(page).toHaveURL(/#\/settings$/);
    await expect(page.getByTestId('unsaved-notice')).toHaveCount(0);
    await page.goto('/#/setup/region');
    await expect(page.getByLabel('Currency (ISO code)')).toHaveValue('EUR');
  });

  test('settings keep unsaved edits and offer to discard them', async ({ page }) => {
    await openStep(page, 'region');
    await page.getByLabel('Currency (ISO code)').fill('CHF');
    await page.getByRole('link', { name: 'Settings' }).click();
    await expect(page).toHaveURL(/#\/settings$/);
    const notice = page.getByTestId('unsaved-notice');
    await expect(notice).toBeVisible();
    await expectNoAxeViolations(page);
    // back to the setup: the edit is still there
    await notice.getByRole('link', { name: 'Back to the setup' }).click();
    await expect(page.getByLabel('Currency (ISO code)')).toHaveValue('CHF');
    await page.getByRole('link', { name: 'Settings' }).click();
    await page.getByTestId('unsaved-discard').click();
    await expect(page.getByTestId('unsaved-notice')).toHaveCount(0);
  });

  test('"Run setup again" restarts the wizard at the first step', async ({ page, request }) => {
    await resetDemo(request); // finished
    await page.goto('/#/settings');
    await expect(page.getByTestId('wizard-status')).toContainText('finished');
    await page.getByTestId('open-wizard').click();
    await expect(page).toHaveURL(/#\/setup\/region$/);
    const wizard = await (await request.get('/api/v1/config/wizard')).json();
    expect(wizard).toMatchObject({ completed: false, completed_steps: [] });
    // the stored configuration stays
    expect((await (await request.get('/api/v1/config/devices')).json()).devices).toHaveLength(3);
  });
});
