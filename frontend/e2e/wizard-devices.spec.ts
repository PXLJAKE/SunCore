// SPDX-License-Identifier: Apache-2.0
import { expect, test } from '@playwright/test';
import { expectNoAxeViolations, openStep, resetDemo } from './helpers';

const device = (id: string, name: string, priority: number) => ({
  id,
  template_id: 'generic.switch',
  name,
  kind: 'switch',
  entities: { switch: { entity_id: 'switch.sim_pool_pump', invert_sign: false } },
  power_model: { kind: 'nominal', nominal_w: 500 },
  control: { kind: 'on_off', role: 'switch' },
  priority,
  min_run_s: 0,
  min_pause_s: 0,
});

test.describe.configure({ mode: 'serial' });

test.describe('devices and priorities', () => {
  test.afterAll(async ({ request }) => resetDemo(request));

  test('gallery filters by kind and search; add, edit and remove a device', async ({
    page,
    request,
  }) => {
    await resetDemo(request, { wizardDone: false });
    await openStep(page, 'devices');
    await page.getByTestId('add-device').click();
    const gallery = page.getByTestId('gallery');
    await expect(gallery.getByRole('listitem')).toHaveCount(4);
    await page.getByLabel('Kind of device').selectOption({ label: 'Air conditioner / climate' });
    await expect(gallery.getByRole('listitem')).toHaveCount(2);
    await page.getByLabel('Search').fill('lg');
    await expect(gallery.getByRole('listitem')).toHaveCount(1);
    await expect(gallery).toContainText('LG ThinQ air conditioner');
    await expect(gallery).toContainText('Not verified with real hardware');
    await page.getByLabel('Search').fill('zzz');
    await expect(gallery).toContainText('No template matches');
    await page.getByLabel('Search').fill('');
    await page.getByLabel('Kind of device').selectOption({ label: 'All kinds' });

    await page.getByRole('button', { name: /^Choose.*Switch or smart plug/ }).click();
    const editor = page.getByTestId('device-editor');
    await page.getByTestId('device-save').click();
    await expect(page.getByTestId('device-errors')).toContainText('Entity still missing');

    await editor.getByLabel('The switch to turn on and off').fill('switch.sim_pool_pump');
    await page.keyboard.press('Escape');
    // the optional power sensor only offers entities of the same registry device
    await editor.getByLabel(/^Power sensor of the same device/).focus();
    await expect(page.getByRole('listbox').getByRole('option')).toHaveCount(1);
    await expect(page.getByRole('listbox').getByRole('option')).toContainText(
      'sensor.sim_pool_pump_power',
    );
    await page.keyboard.press('Escape');
    await editor.getByLabel('Power model').selectOption({ label: 'Fixed nominal power' });
    await editor.getByLabel('Nominal power').fill('750');
    await editor.getByLabel('Name').fill('Pool pump');
    await page.getByTestId('device-save').click();
    await expect(page.getByTestId('devices-notice')).toContainText('Pool pump was added');

    let stored = (await (await request.get('/api/v1/config/devices')).json()).devices;
    expect(stored).toHaveLength(1);
    expect(stored[0]).toMatchObject({
      id: 'pool_pump',
      power_model: { kind: 'nominal', nominal_w: 750 },
    });

    await page.getByRole('button', { name: /^Edit/ }).click();
    await page.getByLabel('Name').fill('Garden pump');
    await page.getByTestId('device-save').click();
    await expect(page.getByTestId('devices-notice')).toContainText('was updated');
    stored = (await (await request.get('/api/v1/config/devices')).json()).devices;
    expect(stored[0]).toMatchObject({ id: 'pool_pump', name: 'Garden pump' });

    await page.getByRole('button', { name: /^Remove/ }).click();
    await page.getByRole('button', { name: /^Really remove/ }).click();
    await expect(page.getByTestId('no-devices')).toBeVisible();
  });

  test('two devices of one template get unique ids', async ({ page, request }) => {
    await resetDemo(request, { wizardDone: false });
    for (let i = 0; i < 2; i += 1) {
      await openStep(page, 'devices');
      await page.getByTestId('add-device').click();
      await page.getByRole('button', { name: /^Choose.*Switch or smart plug/ }).click();
      await page.getByLabel('The switch to turn on and off').fill('switch.sim_pool_pump');
      await page.keyboard.press('Escape');
      await page.getByLabel('Nominal power').fill('100');
      await page.getByLabel('Name').fill('Plug');
      await page.getByTestId('device-save').click();
      // the second one maps the same entity: the server rejects nothing here (devices may share)
      await expect(
        page.getByTestId('devices-notice').or(page.getByTestId('device-server-error')),
      ).toBeVisible();
    }
    const ids = (await (await request.get('/api/v1/config/devices')).json()).devices.map(
      (d: { id: string }) => d.id,
    );
    expect(new Set(ids).size).toBe(ids.length);
  });

  test('priorities: buttons and keyboard reorder, announce, and are stored', async ({
    page,
    request,
  }) => {
    await resetDemo(request, { wizardDone: false });
    await request.put('/api/v1/config/devices', {
      data: {
        schema_version: 1,
        devices: [device('a', 'Alpha', 0), device('b', 'Beta', 1), device('c', 'Gamma', 2)],
      },
    });
    await openStep(page, 'priorities');
    const rows = page.getByTestId('prio-list').getByRole('listitem');
    await expect(rows).toHaveText([/Alpha/, /Beta/, /Gamma/]);
    await expect(page.getByRole('button', { name: 'Move Alpha up' })).toBeDisabled();

    await page.getByRole('button', { name: 'Move Gamma up' }).click();
    await expect(rows).toHaveText([/Alpha/, /Gamma/, /Beta/]);
    await expect(page.getByTestId('prio-live')).toHaveText('Gamma moved to position 2 of 3.');

    const handle = page.getByRole('button', { name: /^Reorder Gamma/ });
    await handle.focus();
    await page.keyboard.press('ArrowUp');
    await expect(rows).toHaveText([/Gamma/, /Alpha/, /Beta/]);
    await expect(page.getByRole('button', { name: /^Reorder Gamma/ })).toBeFocused();
    await expectNoAxeViolations(page);

    await page.getByTestId('wizard-next').click();
    await expect(page).toHaveURL(/#\/setup\/mode$/);
    const stored = (await (await request.get('/api/v1/config/devices')).json()).devices;
    const byPrio = stored.sort(
      (x: { priority: number }, y: { priority: number }) => x.priority - y.priority,
    );
    expect(byPrio.map((d: { id: string }) => d.id)).toEqual(['c', 'a', 'b']);
  });

  test('priorities: drag and drop with the pointer', async ({ page, request }) => {
    await resetDemo(request, { wizardDone: false });
    await request.put('/api/v1/config/devices', {
      data: {
        schema_version: 1,
        devices: [device('a', 'Alpha', 0), device('b', 'Beta', 1), device('c', 'Gamma', 2)],
      },
    });
    await openStep(page, 'priorities');
    const rows = page.getByTestId('prio-list').getByRole('listitem');
    const handle = page.getByRole('button', { name: /^Reorder Alpha/ });
    const from = (await handle.boundingBox())!;
    const lastRow = (await rows.nth(2).boundingBox())!;
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2 + 20, { steps: 4 });
    await page.mouse.move(from.x + from.width / 2, lastRow.y + lastRow.height, { steps: 8 });
    await page.mouse.up();
    await expect(rows).toHaveText([/Beta/, /Gamma/, /Alpha/]);
    await expect(page.getByTestId('prio-live')).toHaveText('Alpha moved to position 3 of 3.');
  });
});
