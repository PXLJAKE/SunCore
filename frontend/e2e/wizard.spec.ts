// SPDX-License-Identifier: Apache-2.0
import { expect, test, type Page } from '@playwright/test';
import { expectNoAxeViolations, openStep, resetDemo, STEP_IDS } from './helpers';

const next = (page: Page) => page.getByTestId('wizard-next');

test.describe.configure({ mode: 'serial' });

test.afterAll(async ({ request }) => {
  await resetDemo(request);
});

test.describe('setup wizard', () => {
  test.beforeEach(async ({ request }) => {
    await resetDemo(request, { wizardDone: false });
  });

  test('first start opens the wizard; full walk-through, axe on every step', async ({
    page,
    request,
  }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/#\/setup\/region$/);
    await expect(
      page.getByRole('heading', { name: 'Region and language', level: 2 }),
    ).toBeFocused();
    await expect(page.getByTestId('wizard-count')).toBeAttached();

    // 1 region
    await page.getByLabel('Region profile').selectOption({ label: 'Germany' });
    await expect(page.getByLabel('Currency (ISO code)')).toHaveValue('EUR');
    await expect(page.getByLabel('Time zone')).toHaveValue('Europe/Berlin');
    await expectNoAxeViolations(page);
    await next(page).click();

    // 2 energy dashboard
    await expect(page).toHaveURL(/#\/setup\/energy$/);
    await expect(page.getByTestId('energy-suggestions')).toBeVisible();
    await expect(
      page.getByTestId('energy-suggestions').getByRole('listitem').first(),
    ).toContainText('Energy dashboard');
    await expectNoAxeViolations(page);
    await next(page).click();

    // 3 grid
    await expect(page).toHaveURL(/#\/setup\/grid$/);
    const grid = page.getByLabel('Grid power (positive = import)');
    await expect(grid).toHaveValue('sensor.sim_grid_power');
    await expect(page.getByTestId('sign-question')).toContainText(/grid connection is/i);
    await page.getByRole('button', { name: 'Yes, correct' }).click();
    await expectNoAxeViolations(page);
    await next(page).click();

    // 4 solar
    await expect(page).toHaveURL(/#\/setup\/pv$/);
    await page.getByLabel('Installed power').fill('9.8');
    await page.getByLabel('Solcast PV forecast').check();
    await expect(page.getByLabel(/^Solcast sensor for today/).first()).toBeVisible();
    await expectNoAxeViolations(page);
    await next(page).click();

    // 5 battery
    await expect(page).toHaveURL(/#\/setup\/battery$/);
    await expect(page.getByTestId('sign-question')).toContainText(/battery is/i);
    await expect(page.getByTestId('observe-note')).toBeVisible();
    await page.getByLabel('Usable capacity').fill('10');
    await expectNoAxeViolations(page);
    await next(page).click();

    // 6 tariff
    await expect(page).toHaveURL(/#\/setup\/tariff$/);
    // the demo is set up with a dynamic tariff: switch to a fixed price
    await page.getByRole('group', { name: 'Import price' }).getByLabel('Fixed price').check();
    await page
      .getByRole('group', { name: 'Import price' })
      .getByLabel(/^Price per kWh/)
      .fill('0.32');
    await page
      .getByRole('group', { name: 'Feed-in remuneration' })
      .getByLabel(/^Price per kWh/)
      .fill('0.08');
    await expectNoAxeViolations(page);
    await next(page).click();

    // 7 devices
    await expect(page).toHaveURL(/#\/setup\/devices$/);
    await expect(page.getByTestId('no-devices')).toBeVisible();
    await page.getByTestId('add-device').click();
    await expect(page.getByTestId('gallery')).toBeVisible();
    await page.getByLabel('Search').fill('plug');
    await expect(page.getByTestId('gallery').getByRole('listitem')).toHaveCount(1);
    await expectNoAxeViolations(page);
    await page.getByRole('button', { name: /^Choose/ }).click();
    const editor = page.getByTestId('device-editor');
    await expect(editor).toBeVisible();
    await editor.getByLabel('The switch to turn on and off').fill('switch.sim_pool_pump');
    await page.keyboard.press('Escape');
    await editor.getByLabel(/^Power sensor of the same device/).fill('sensor.sim_pool_pump_power');
    await page.keyboard.press('Escape');
    await expectNoAxeViolations(page);
    await page.getByTestId('device-save').click();
    await expect(page.getByTestId('devices-notice')).toContainText('was added');
    await expect(page.getByTestId('device-list').getByRole('listitem')).toHaveCount(1);
    await next(page).click();

    // 8 priorities and goals
    await expect(page).toHaveURL(/#\/setup\/priorities$/);
    await expect(page.getByTestId('prio-list').getByRole('listitem')).toHaveCount(1);
    await page.getByLabel('Have the battery at a target charge by a time').check();
    await page.getByLabel('Target charge', { exact: true }).fill('90');
    await expectNoAxeViolations(page);
    await next(page).click();

    // 9 mode
    await expect(page).toHaveURL(/#\/setup\/mode$/);
    await expect(page.getByTestId('mode-note')).toContainText('Only shadow mode is active');
    await page.getByRole('radio', { name: /Assisted mode/ }).check();
    await expectNoAxeViolations(page);
    await next(page).click();

    // 10 summary
    await expect(page).toHaveURL(/#\/setup\/summary$/);
    await expect(page.getByTestId('check-items')).toBeVisible();
    await expect(page.getByTestId('check-overall')).toHaveAttribute('data-severity', /ok|warn/);
    await expect(page.getByTestId('plan-note')).toContainText('later version');
    await expectNoAxeViolations(page);
    await next(page).click();
    await expect(page).toHaveURL(/#\/$/);

    // everything stored
    const get = async (s: string) => (await request.get(`/api/v1/config/${s}`)).json();
    expect(await get('region')).toMatchObject({ region_id: 'de', currency: 'EUR' });
    expect((await get('site')).pv[0].kwp).toBe(9.8);
    expect((await get('site')).battery.capacity_wh).toBe(10000);
    expect(await get('tariff')).toMatchObject({
      import: { kind: 'fixed', fixed_price: 0.32 },
      export: { kind: 'fixed', fixed_price: 0.08 },
    });
    expect(await get('forecast')).toMatchObject({ forecast_template_ids: ['solcast.pv'] });
    const devices = (await get('devices')).devices;
    expect(devices).toHaveLength(1);
    expect(devices[0]).toMatchObject({ template_id: 'generic.switch', priority: 0 });
    expect(await get('goals')).toMatchObject({ battery_target: { soc_pct: 90 } });
    expect(await get('operation')).toMatchObject({ mode: 'assisted' });
    expect(await get('wizard')).toMatchObject({ completed: true });
    expect((await get('wizard')).completed_steps).toEqual([...STEP_IDS]);

    // finished: no redirect any more
    await page.goto('/');
    await expect(page).toHaveURL(/\/$|#\/$/);
    await expect(page.getByRole('heading', { name: 'Now', level: 2 })).toBeVisible();
  });

  test('resumes at the first step that is not done', async ({ page, request }) => {
    await request.put('/api/v1/config/wizard', {
      data: {
        schema_version: 1,
        completed: false,
        completed_steps: ['region', 'energy', 'grid'],
        version: 1,
      },
    });
    await page.goto('/#/setup');
    await expect(page).toHaveURL(/#\/setup\/pv$/);
    // On narrow screens the step list is collapsed behind a button.
    const toggle = page.getByRole('button', { name: 'Show all steps' });
    if (await toggle.isVisible()) {
      await expect(toggle).toHaveAttribute('aria-expanded', 'false');
      await toggle.click();
    }
    await expect(page.getByRole('link', { name: /Grid connection/ })).toContainText('✓');
    await expect(page.getByRole('link', { name: /Solar/ })).toHaveAttribute('aria-current', 'step');
  });

  test('skip, back and the step list navigate without saving', async ({ page, request }) => {
    await openStep(page, 'energy');
    await page.getByTestId('wizard-skip').click();
    await expect(page).toHaveURL(/#\/setup\/grid$/);
    await page.getByTestId('wizard-back').click();
    await expect(page).toHaveURL(/#\/setup\/energy$/);
    expect((await (await request.get('/api/v1/config/wizard')).json()).completed_steps).toEqual([]);
  });

  test('a server validation error is shown next to the field and in the summary', async ({
    page,
  }) => {
    await openStep(page, 'grid');
    await page.getByLabel('Grid power (positive = import)').fill('not an entity');
    await next(page).click();
    await expect(page.getByTestId('save-error')).toContainText('Some values are invalid');
    await expect(page.getByTestId('field-issues').first()).toBeVisible();
    await expect(page).toHaveURL(/#\/setup\/grid$/);
  });

  test('every step renders without axe violations', async ({ page }) => {
    for (const step of STEP_IDS) {
      await openStep(page, step);
      await expect(page.getByTestId('wizard-next')).toBeVisible();
      await expectNoAxeViolations(page);
    }
  });

  test('sign check inverts the sensor with one click', async ({ page }) => {
    await openStep(page, 'battery');
    const power = page.getByLabel('Battery power (positive = charging)');
    await expect(power).toHaveValue('sensor.sim_battery_power');
    // The demo battery may be idle at the moment, so only the question itself is asserted.
    await expect(page.getByTestId('sign-question')).toContainText(/The battery is/);
    await page.getByTestId('sign-invert').click();
    await expect(page.getByLabel('Invert sign').first()).toBeChecked();
    await expect(page.getByText('Sign inverted.')).toBeVisible();
    await next(page).click();
    await expect(page).toHaveURL(/#\/setup\/tariff$/);
  });
});

test.describe('role picker', () => {
  test('shows ranking reasons, value and device for each candidate', async ({ page, request }) => {
    await resetDemo(request, { wizardDone: false });
    await openStep(page, 'battery');
    const soc = page.getByLabel('Battery state of charge (%)');
    await soc.focus();
    const option = page.getByRole('option', { name: /Sim battery SoC/ });
    await expect(option).toBeVisible();
    await expect(option).toContainText('sensor.sim_battery_soc');
    await expect(option).toContainText('SunCore demo house');
    await expect(option).toContainText('unit');
    await expect(option).toContainText('Best match');
    expect((await option.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await soc.press('ArrowDown');
    await soc.press('Enter');
    await expect(soc).toHaveValue('sensor.sim_battery_soc');
    await expect(soc).toHaveAttribute('aria-expanded', 'false');
  });
});
