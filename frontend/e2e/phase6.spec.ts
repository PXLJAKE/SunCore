// SPDX-License-Identifier: Apache-2.0
// PR 6-M: grid chain in the wizard, Phase 6 templates, EV and program cards, settings sections.
import { expect, test, type APIRequestContext } from '@playwright/test';
import { expectNoAxeViolations, openStep, resetDemo } from './helpers';

test.describe.configure({ mode: 'serial' });

const ref = (entity_id: string) => ({ entity_id, invert_sign: false });

async function seedDevices(request: APIRequestContext): Promise<void> {
  const base = {
    priority: 1,
    min_run_s: 0,
    min_pause_s: 0,
    power_model: { kind: 'nominal', nominal_w: 2000 },
    windows: [],
  };
  const res = await request.put('/api/v1/config/devices', {
    data: {
      schema_version: 1,
      devices: [
        {
          ...base,
          id: 'garage_wallbox',
          template_id: 'generic.wallbox',
          name: 'Garage wallbox',
          kind: 'wallbox',
          entities: { current: ref('number.sim_wallbox_current') },
          control: { kind: 'charger', role: 'current', max_current_a: 16 },
          ev: { default_mode: 'pv' },
        },
        {
          ...base,
          id: 'washer',
          template_id: 'generic.program_plug',
          name: 'Washer',
          kind: 'program',
          entities: { switch: ref('switch.sim_pool_pump') },
          control: { kind: 'on_off', role: 'switch' },
          program: {
            runtime_min: 120,
            start_method: 'plug',
            idle_w: 5,
            resume_confirmed: true,
          },
          behaviour_confirmed: true,
        },
      ],
    },
  });
  expect(res.ok(), `PUT devices: ${res.status()} ${await res.text()}`).toBe(true);
}

test.describe('Phase 6 wizard', () => {
  test.afterAll(async ({ request }) => resetDemo(request));

  test('gallery lists wallbox, evcc, heat pump and program templates', async ({
    page,
    request,
  }) => {
    await resetDemo(request, { wizardDone: false });
    await openStep(page, 'devices');
    await page.getByTestId('add-device').click();
    const gallery = page.getByTestId('gallery');
    const kind = page.getByLabel('Kind of device');
    await kind.selectOption({ label: 'Wallbox' });
    await expect(gallery.getByRole('listitem')).toHaveCount(2);
    await expect(gallery).toContainText('evcc loadpoint (MQTT)');
    await kind.selectOption({ label: 'Program' });
    await expect(gallery.getByRole('listitem')).toHaveCount(3);
    await kind.selectOption({ label: 'Heat pump' });
    await expect(gallery.getByRole('listitem')).toHaveCount(2);
    await expectNoAxeViolations(page);
  });

  test('evcc editor shows loadpoint id and prefix and asks to confirm the behaviour', async ({
    page,
    request,
  }) => {
    await resetDemo(request, { wizardDone: false });
    await openStep(page, 'devices');
    await page.getByTestId('add-device').click();
    await page.getByRole('button', { name: /^Choose.*evcc loadpoint/ }).click();
    const editor = page.getByTestId('device-editor');
    await expect(editor.getByLabel('evcc loadpoint number')).toBeVisible();
    await expect(editor.getByLabel('evcc MQTT topic prefix')).toHaveValue('evcc');
    await expect(page.getByTestId('behaviour-confirm')).toBeVisible();
    await expect(page.getByTestId('behaviour-confirmed')).not.toBeChecked();
    await expectNoAxeViolations(page);
  });

  test('program editor shows run time, idle power and the peak placeholder', async ({
    page,
    request,
  }) => {
    await resetDemo(request, { wizardDone: false });
    await openStep(page, 'devices');
    await page.getByTestId('add-device').click();
    await page.getByRole('button', { name: /^Choose.*Shiftable program \(smart plug\)/ }).click();
    const spec = page.getByTestId('program-spec');
    await expect(spec.getByLabel('Run time of one program')).toHaveValue('120');
    await expect(spec.getByLabel('Finished below')).toHaveValue('5');
    await expect(page.getByTestId('program-peak')).toContainText('nominal power');
    await expect(page.getByTestId('resume-confirmed')).not.toBeChecked();
  });

  test('grid step stores peak shaving, export chain, §14a limit and the backup entity', async ({
    page,
    request,
  }) => {
    await resetDemo(request, { wizardDone: false });
    await openStep(page, 'grid');
    await page.getByLabel('Keep the monthly grid peak low').check();
    await page.getByLabel('Peak floor').fill('3000');
    await page.getByLabel('Zero export').check();
    await page.getByLabel('My installation is controllable under section 14a').check();
    await page.getByLabel('Limit while the signal is active').fill('3600');
    await page.getByLabel('My system can run as an island').check();
    await page.getByLabel('Backup state entity').fill('sensor.sim_backup_state');
    await page.keyboard.press('Escape');
    await page.getByLabel('States that mean island operation').fill('island, backup');
    await expectNoAxeViolations(page);
    await page.getByTestId('wizard-next').click();
    await expect(page.getByTestId('wizard')).toHaveAttribute('data-step', 'pv');
    await expect
      .poll(async () => (await (await request.get('/api/v1/config/site')).json()).grid)
      .toMatchObject({
        peak_shaving: { enabled: true, peak_floor_w: 3000 },
        zero_export: true,
        para14a: true,
        para14a_limit_w: 3600,
        backup: { entity_id: 'sensor.sim_backup_state', island_states: ['island', 'backup'] },
      });
  });

  test('step 5 shows the inverter limit disabled with a reason', async ({ page, request }) => {
    await resetDemo(request, { wizardDone: false });
    await openStep(page, 'battery');
    const block = page.getByTestId('inverter-limit');
    await expect(block).toBeVisible();
    await expect(page.getByTestId('inverter-why')).toContainText('Not available yet');
    await expect(block.getByRole('button', { name: 'Run the verification test' })).toBeDisabled();
  });
});

test.describe('Phase 6 cards', () => {
  test.afterAll(async ({ request }) => resetDemo(request));

  test('EV card sets a mode and a target through the API', async ({ page, request }) => {
    await resetDemo(request);
    await seedDevices(request);
    await page.goto('/');
    const section = page.getByTestId('ev-section-garage_wallbox');
    await expect(section).toBeVisible();
    await section.getByTestId('ev-mode-now').click();
    await expect(section.getByTestId('ev-mode-now')).toHaveAttribute('aria-pressed', 'true');
    let flex = await (await request.get('/api/v1/devices/garage_wallbox/flex')).json();
    expect(flex.ev.mode).toBe('now');

    await section.getByTestId('ev-mode-target').click();
    await section.getByTestId('ev-target-value').fill('75');
    await section.getByTestId('ev-target-apply').click();
    await expect(section.getByTestId('ev-target')).toContainText('75');
    flex = await (await request.get('/api/v1/devices/garage_wallbox/flex')).json();
    expect(flex.ev).toMatchObject({ mode: 'target', target: { soc_pct: 75 } });
    await expectNoAxeViolations(page);
  });

  test('program card arms a job and warns before cancelling it', async ({ page, request }) => {
    await resetDemo(request);
    await seedDevices(request);
    await page.goto('/');
    const section = page.getByTestId('program-section-washer');
    await expect(section).toBeVisible();
    await section.getByTestId('program-arm').click();
    await expect(section.getByTestId('program-job-state')).toBeVisible();
    const flex = await (await request.get('/api/v1/devices/washer/flex')).json();
    expect(flex.job).not.toBeNull();

    await section.getByTestId('program-cancel').click();
    const dialog = page.getByTestId('program-cancel-dialog-washer');
    await expect(dialog.getByTestId('program-cancel-warning')).toContainText('starts immediately');
    await expectNoAxeViolations(page);
    await dialog.getByRole('button', { name: 'Cancel job' }).click();
    await expect(section.getByTestId('program-arm')).toBeVisible();
  });
});

test.describe('Phase 6 settings', () => {
  test.afterAll(async ({ request }) => resetDemo(request));

  test('Home Assistant entities: status, hint without a broker, mode-raise opt-in', async ({
    page,
    request,
  }) => {
    await resetDemo(request);
    await page.goto('/#/settings');
    const box = page.getByTestId('ha-entities');
    await expect(box.getByTestId('ha-status')).toBeVisible();
    await expect(box.getByTestId('ha-republish')).toBeDisabled();
    const raise = box.getByTestId('ha-allow-raise');
    await expect(raise).not.toBeChecked();
    await raise.check();
    await expect(box.getByTestId('ha-message')).toContainText('saved');
    const mqtt = await (await request.get('/api/v1/config/mqtt')).json();
    expect(mqtt.allow_mode_raise).toBe(true);
    await expectNoAxeViolations(page);
  });

  test('Notifications: categories, a push target, quiet hours and the test message', async ({
    page,
    request,
  }) => {
    await resetDemo(request);
    await page.goto('/#/settings');
    const box = page.getByTestId('notifications');
    await expect(box.getByTestId('notify-status')).toBeVisible();
    await box.getByTestId('notify-test').click();
    await expect(box.getByTestId('notify-test-result')).toContainText('No push target');
    await box.getByTestId('notify-cat-daily_summary').check();
    await box.getByTestId('notify-service').fill('mobile_app_phone');
    await box.getByTestId('notify-add-service').click();
    await expect(box.getByTestId('notify-targets')).toContainText('mobile_app_phone');
    await box.getByTestId('notify-save').click();
    await expect(box.getByTestId('notify-message')).toContainText('saved');
    const cfg = await (await request.get('/api/v1/notifications/settings')).json();
    expect(cfg.categories.daily_summary).toBe(true);
    expect(cfg.targets).toEqual([{ kind: 'service', id: 'mobile_app_phone' }]);
    await expectNoAxeViolations(page);
  });

  test('History lists grid events and the monthly peak section', async ({ page, request }) => {
    await resetDemo(request);
    await page.goto('/#/history');
    await expect(page.getByTestId('history-grid')).toBeVisible();
    await expectNoAxeViolations(page);
  });
});
