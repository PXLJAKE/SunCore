// SPDX-License-Identifier: Apache-2.0
// The Phase 4 wizard steps against the demo: sources (4), battery (5), tariff (6), goals and
// grid charging (8), summary with the first plan (10). Each step is saved, read back from the API
// and shown again after a reload.
import { expect, test, type APIRequestContext } from '@playwright/test';
import { expectNoAxeViolations, openStep, resetDemo } from './helpers';

test.describe.configure({ mode: 'serial' });

const next = (page: import('@playwright/test').Page) => page.getByTestId('wizard-next');
const get = async (request: APIRequestContext, section: string) =>
  (await request.get(`/api/v1/config/${section}`)).json();

test.beforeEach(async ({ request }) => {
  await resetDemo(request);
});
test.afterAll(async ({ request }) => {
  await resetDemo(request);
});

test('step 4: several sources, entry choice, fixed share and the load inputs', async ({
  page,
  request,
}) => {
  await openStep(page, 'pv');
  await expect(page.getByTestId('forecast-count')).toContainText('3 forecast sources');
  const entry = page.getByTestId('entry-field').getByRole('combobox');
  await expect(entry).toContainText('sim_entry_forecast_solar');
  await entry.selectOption('sim_entry_forecast_solar');
  const solcast = page.getByRole('group', { name: 'Weight in the blend' }).last();
  await solcast.getByRole('radio', { name: 'Fixed share' }).check();
  await solcast.getByLabel('Share (0–1)').fill('0.6');
  await expect(page.getByTestId('open-meteo-note')).toBeVisible();
  await expectNoAxeViolations(page);
  await next(page).click();
  await expect(page).toHaveURL(/#\/setup\/battery$/);
  const forecast = await get(request, 'forecast');
  const solar = forecast.sources.find(
    (s: { template_id: string }) => s.template_id === 'forecast_solar.pv',
  );
  expect(solar.config_entry_id).toBe('sim_entry_forecast_solar');
  const sol = forecast.sources.find((s: { template_id: string }) => s.template_id === 'solcast.pv');
  expect(sol).toMatchObject({ weight_mode: 'manual', weight: 0.6 });
  expect(forecast.load.workday.entity_id).toBe('binary_sensor.sim_workday_sensor');
  // Shown again after a reload.
  await page.goto('/#/setup/pv');
  await page.reload();
  await expect(
    page.getByRole('group', { name: 'Weight in the blend' }).last().getByLabel('Share (0–1)'),
  ).toHaveValue('0.6');
});

test('step 5: adapter, model, strategy and risk level round trip', async ({ page, request }) => {
  await openStep(page, 'battery');
  const control = page.getByTestId('battery-control');
  await expect(control.getByRole('combobox', { name: 'Battery adapter' })).toContainText('generic');
  await control
    .getByRole('combobox', { name: 'Battery adapter' })
    .selectOption('kostal.plenticore');
  await expect(page.getByTestId('hint-kostal-rest')).toContainText('Battery:MinSoc');
  await control
    .getByRole('combobox', { name: 'Battery adapter' })
    .selectOption('kostal.plenticore_g3_modbus');
  await expect(page.getByTestId('hint-kostal-modbus')).toContainText('modbus hub');
  await expect(page.getByTestId('fallback-state')).toContainText('not been measured');
  // Observe only keeps the model.
  await control.getByRole('combobox', { name: 'Battery adapter' }).selectOption('');
  await expect(page.getByTestId('observe-note')).toBeVisible();
  const model = page.getByTestId('battery-model');
  await model.getByLabel('Reserve (backup energy)').fill('15');
  await model.getByLabel('Maximum state of charge').fill('95');
  const strategy = page.getByTestId('battery-strategy');
  await strategy.getByRole('radio', { name: 'Keep the battery as full as possible' }).check();
  await strategy.getByRole('radio', { name: 'Balanced' }).check();
  await strategy.getByLabel('Minimum morning buffer').fill('25');
  await strategy.getByLabel('Bridge into the next day').uncheck();
  await expectNoAxeViolations(page);
  await next(page).click();
  await expect(page).toHaveURL(/#\/setup\/tariff$/);
  const battery = await get(request, 'battery');
  expect(battery).toMatchObject({
    adapter_template_id: null,
    strategy: 'prefer_full',
    risk: 'balanced',
    morning_buffer_pct: 25,
    bridge_next_day: false,
  });
  expect(battery.model).toMatchObject({ reserve_pct: 15, max_soc_pct: 95, capacity_wh: 10000 });
  await page.goto('/#/setup/battery');
  await page.reload();
  await expect(
    page.getByTestId('battery-strategy').getByRole('radio', { name: 'Balanced' }),
  ).toBeChecked();
});

test('step 5: the adapter with its entities is stored', async ({ page, request }) => {
  await openStep(page, 'battery');
  const control = page.getByTestId('battery-control');
  await expect(control.getByLabel(/Minimum state of charge/)).toHaveValue(
    'number.sim_battery_min_soc',
  );
  await next(page).click();
  await expect(page).toHaveURL(/#\/setup\/tariff$/);
  const battery = await get(request, 'battery');
  expect(battery.adapter_template_id).toBe('generic.battery');
  expect(battery.entities.min_soc_number.entity_id).toBe('number.sim_battery_min_soc');
});

test('step 6: HT/NT tariff editor round trip', async ({ page, request }) => {
  await openStep(page, 'tariff');
  const imp = page.getByRole('group', { name: 'Import price' });
  await imp.getByRole('radio', { name: 'Fixed time-of-use (HT/NT)' }).check();
  const editor = page.getByTestId('tou-editor');
  await editor.getByLabel(/^Base price/).fill('0.34');
  await editor.getByTestId('tou-add').click();
  const period = editor.getByTestId('tou-period').first();
  await period.getByLabel(/^Price in this period/).fill('0.22');
  await period.getByLabel('Sat').uncheck();
  await period.getByLabel('Sun').uncheck();
  await expectNoAxeViolations(page);
  await next(page).click();
  await expect(page).toHaveURL(/#\/setup\/devices$/);
  const tariff = await get(request, 'tariff');
  expect(tariff.import.kind).toBe('tou');
  expect(tariff.import.tou.base_price).toBe(0.34);
  expect(tariff.import.tou.periods[0]).toMatchObject({
    price: 0.22,
    window: { days: [0, 1, 2, 3, 4], start: '22:00', end: '06:00' },
  });
  await page.goto('/#/setup/tariff');
  await expect(page.getByTestId('tou-editor').getByLabel(/^Base price/)).toHaveValue('0.34');
});

test('step 6: a spot-only price gets its formula', async ({ page, request }) => {
  await openStep(page, 'tariff');
  const imp = page.getByRole('group', { name: 'Import price' });
  await imp.getByRole('radio', { name: 'Dynamic price', exact: true }).check();
  await imp.getByRole('radio', { name: 'Only a price sensor' }).check();
  const picker = imp.getByRole('combobox', { name: 'Price sensor' });
  await picker.fill('sensor.sim_electricity_price');
  await page.keyboard.press('Escape');
  await page.getByTestId('price-includes').getByRole('combobox').selectOption('spot_only');
  const formula = page.getByTestId('formula-editor');
  await expect(formula).toBeVisible();
  await formula.getByRole('checkbox', { name: /Turn the spot price/ }).check();
  await formula.getByLabel(/^Grid fee per kWh/).fill('0.08');
  await formula.getByLabel('VAT').fill('19');
  await formula.getByText('Time-variable grid fees').click();
  await formula.getByRole('button', { name: 'Add a grid fee window' }).click();
  await formula.getByLabel(/^Grid fee in this window/).fill('0.12');
  await expectNoAxeViolations(page);
  await next(page).click();
  await expect(page).toHaveURL(/#\/setup\/devices$/);
  const tariff = await get(request, 'tariff');
  expect(tariff.import).toMatchObject({
    kind: 'dynamic',
    price_includes: 'spot_only',
    formula: { grid_fee: 0.08, vat_pct: 19 },
  });
  expect(tariff.import.formula.grid_fee_windows).toHaveLength(1);
});

test('step 8: grid-charge policy with price level and schedule, autarky weight', async ({
  page,
  request,
}) => {
  await openStep(page, 'priorities');
  const gc = page.getByTestId('grid-charge');
  await expect(gc.getByTestId('gc-enable')).not.toBeChecked();
  await gc.getByTestId('gc-enable').check();
  await gc.getByLabel(/^Charge only at or below/).fill('0.12');
  await gc.getByRole('button', { name: 'Fill with the night window 22:00–06:00' }).click();
  await expect(
    gc.getByTestId('gc-schedule').getByRole('group', { name: 'Window 1' }),
  ).toBeVisible();
  await gc.getByText('Self-sufficiency weight per kWh').first().click();
  await gc.getByLabel('Self-sufficiency weight per kWh').last().fill('0.02');
  await expectNoAxeViolations(page);
  await next(page).click();
  await expect(page).toHaveURL(/#\/setup\/mode$/);
  const battery = await get(request, 'battery');
  expect(battery.grid_charge).toMatchObject({
    enabled: true,
    max_price: 0.12,
    schedule: { windows: [{ days: [0, 1, 2, 3, 4, 5, 6], start: '22:00', end: '06:00' }] },
  });
  expect(battery.autarky_weight_per_kwh).toBe(0.02);
});

test('step 8: grid charging without a rule is refused by the step', async ({ page }) => {
  await openStep(page, 'priorities');
  await page.getByTestId('gc-enable').check();
  await next(page).click();
  await expect(page.getByRole('alert')).toContainText('price level or a schedule');
  await expect(page).toHaveURL(/#\/setup\/priorities$/);
});

test('step 10: the first plan preview', async ({ page }) => {
  await openStep(page, 'summary');
  const preview = page.getByTestId('plan-preview');
  await expect(preview.getByTestId('plan-preview-source')).toHaveText('Optimiser');
  await expect(preview.getByTestId('plan-preview-numbers')).toContainText('solar');
  await expect(preview.getByTestId('plan-preview-soc')).toContainText('Battery');
  await preview.getByRole('link', { name: 'Open the plan' }).click();
  await expect(page).toHaveURL(/#\/plan$/);
});

test('settings link to the steps and show the new summaries', async ({ page }) => {
  await page.goto('/#/settings');
  const overview = page.getByTestId('overview');
  await expect(overview).toContainText('forecast source');
  await expect(overview).toContainText('Control:');
});

test('German labels of the new steps', async ({ browser }) => {
  const ctx = await browser.newContext({ locale: 'de-DE' });
  const page = await ctx.newPage();
  await page.goto('/#/setup/battery');
  await expect(page.getByTestId('battery-strategy')).toContainText('Risikostufe');
  await page.goto('/#/setup/priorities');
  await expect(page.getByTestId('grid-charge')).toContainText('Laden aus dem Netz');
  await ctx.close();
});

test('step 6: a stored price formula survives saving at once, before the template has loaded', async ({
  page,
  request,
}) => {
  const formula = {
    spot_multiplier: 1,
    grid_fee: 0.08,
    levies: 0.03,
    margin: 0.01,
    vat_pct: 19,
    grid_fee_windows: [],
  };
  const put = await request.put('/api/v1/config/tariff', {
    data: {
      schema_version: 3,
      import: {
        kind: 'dynamic',
        template_id: 'entsoe.price',
        price_entity: { entity_id: 'sensor.sim_electricity_price', invert_sign: false },
        formula,
      },
      export: { kind: 'fixed', fixed_price: 0.08, tiers: [] },
    },
  });
  expect(put.ok()).toBe(true);
  // Slow template answers: the step cannot know the source yet when "save" is pressed.
  await page.route('**/api/v1/templates/entsoe.price', async (route) => {
    await new Promise((r) => setTimeout(r, 1500));
    await route.continue();
  });
  await openStep(page, 'tariff');
  await next(page).click();
  await expect(page).toHaveURL(/#\/setup\/devices$/);
  const tariff = await get(request, 'tariff');
  expect(tariff.import.formula).toMatchObject({ grid_fee: 0.08, vat_pct: 19 });
});

test('step 6: a period without a price blocks saving', async ({ page }) => {
  await openStep(page, 'tariff');
  const imp = page.getByRole('group', { name: 'Import price' });
  await imp.getByRole('radio', { name: 'Fixed time-of-use (HT/NT)' }).check();
  const editor = page.getByTestId('tou-editor');
  await editor.getByLabel(/^Base price/).fill('0.34');
  await editor.getByTestId('tou-add').click();
  await next(page).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page).toHaveURL(/#\/setup\/tariff$/);
});
