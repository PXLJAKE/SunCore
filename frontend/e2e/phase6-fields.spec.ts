// SPDX-License-Identifier: Apache-2.0
// 7-Q: the Phase 6 fields in Settings: per-device para14a and shed_rank, the island keep list and
// the manual MQTT broker (password write-only). Edit, save, read back through the API, and the
// validation errors of the backend.
import { expect, test, type APIRequestContext } from '@playwright/test';
import { expectNoAxeViolations, resetDemo } from './helpers';

test.describe.configure({ mode: 'serial' });

interface Device {
  id: string;
  name: string;
  para14a?: boolean;
  shed_rank?: number | null;
}

async function devices(request: APIRequestContext): Promise<Device[]> {
  const body = await (await request.get('/api/v1/config/devices')).json();
  return body.devices as Device[];
}

test.describe('Phase 6 fields in Settings', () => {
  test.afterAll(async ({ request }) => resetDemo(request));

  test('devices: para14a and shed rank are saved', async ({ page, request }) => {
    await resetDemo(request);
    const before = await devices(request);
    const first = before[0]!;
    const second = before[1]!;
    await page.goto('/#/settings/devices');
    const box = page.getByTestId('device-grid-settings');
    await expect(box).toBeVisible();
    await expect(box.getByTestId('device-grid-save')).toBeDisabled();

    await box.getByTestId(`para14a-${first.id}`).check();
    await box.getByTestId(`shed-rank-${second.id}`).fill('3');
    await box.getByTestId('device-grid-save').click();
    await expect(box.getByTestId('device-grid-saved')).toBeVisible();

    const after = await devices(request);
    expect(after.find((d) => d.id === first.id)?.para14a).toBe(true);
    expect(after.find((d) => d.id === second.id)?.shed_rank).toBe(3);
    // Everything else of the devices is untouched.
    expect(after.map((d) => d.id)).toEqual(before.map((d) => d.id));
    await expectNoAxeViolations(page);

    // Emptying the rank goes back to the automatic order (null).
    await box.getByTestId(`shed-rank-${second.id}`).fill('');
    await box.getByTestId('device-grid-save').click();
    await expect(box.getByTestId('device-grid-saved')).toBeVisible();
    expect((await devices(request)).find((d) => d.id === second.id)?.shed_rank ?? null).toBeNull();
  });

  test('devices: an out-of-range rank shows the backend error and stores nothing', async ({
    page,
    request,
  }) => {
    await resetDemo(request);
    const target = (await devices(request))[0]!;
    await page.goto('/#/settings/devices');
    const box = page.getByTestId('device-grid-settings');
    await box.getByTestId(`shed-rank-${target.id}`).fill('20000');
    await box.getByTestId('device-grid-save').click();
    const input = box.getByTestId(`shed-rank-${target.id}`);
    await expect(input).toHaveAttribute('aria-invalid', 'true');
    await expect(box.getByTestId('device-grid-saved')).toHaveCount(0);
    expect((await devices(request)).find((d) => d.id === target.id)?.shed_rank ?? null).toBeNull();
    await expectNoAxeViolations(page);
  });

  test('island: keep list needs a backup entity, then saves into grid.backup', async ({
    page,
    request,
  }) => {
    await resetDemo(request);
    await page.goto('/#/settings/devices');
    await expect(page.getByTestId('island-no-backup')).toBeVisible();

    const site = await (await request.get('/api/v1/config/site')).json();
    site.grid.backup = {
      entity_id: 'sensor.sim_inverter_mode',
      island_states: ['island'],
      keep_devices: [],
    };
    const put = await request.put('/api/v1/config/site', { data: site });
    expect(put.ok(), await put.text()).toBe(true);

    await page.reload();
    const box = page.getByTestId('island-keep');
    await expect(box.getByTestId('island-save')).toBeDisabled();
    const dev = (await devices(request))[0]!;
    await box.getByTestId(`keep-${dev.id}`).check();
    await box.getByTestId('island-save').click();
    await expect(box.getByTestId('island-saved')).toBeVisible();
    const stored = await (await request.get('/api/v1/config/site')).json();
    expect(stored.grid.backup.keep_devices).toEqual([dev.id]);
    expect(stored.grid.backup.island_states).toEqual(['island']);
    await expectNoAxeViolations(page);

    await box.getByTestId(`keep-${dev.id}`).uncheck();
    await box.getByTestId('island-save').click();
    await expect(box.getByTestId('island-saved')).toBeVisible();
    expect(
      (await (await request.get('/api/v1/config/site')).json()).grid.backup.keep_devices,
    ).toEqual([]);
  });

  test('MQTT broker: manual settings, write-only password, validation', async ({
    page,
    request,
  }) => {
    await resetDemo(request);
    await page.goto('/#/settings/ha');
    const box = page.getByTestId('mqtt-broker');
    await expect(box).toBeVisible();
    await expect(box.getByTestId('mqtt-save')).toBeDisabled();

    // A manual broker without a host is refused by the backend.
    await box.getByTestId('mqtt-broker-manual').check();
    await box.getByTestId('mqtt-save').click();
    await expect(box.getByTestId('mqtt-error')).toBeVisible();
    await expectNoAxeViolations(page);

    // A password without a user name is refused too.
    await box.getByTestId('mqtt-host').fill('broker.example');
    await box.getByTestId('mqtt-password').fill('s3cret-pw');
    await box.getByTestId('mqtt-save').click();
    await expect(box.getByTestId('mqtt-error')).toBeVisible();

    await box.getByTestId('mqtt-username').fill('sun');
    await box.getByTestId('mqtt-port').fill('8883');
    await box.getByTestId('mqtt-tls').check();
    await box.getByTestId('mqtt-save').click();
    await expect(box.getByTestId('mqtt-saved')).toBeVisible();

    const cfg = await (await request.get('/api/v1/config/mqtt')).json();
    expect(cfg).toMatchObject({
      broker: 'manual',
      host: 'broker.example',
      port: 8883,
      tls: true,
      username: 'sun',
    });
    // The API only ever returns the mask; the page never shows the secret.
    expect(cfg.password).not.toBe('s3cret-pw');
    await expect(box.getByTestId('mqtt-password')).toHaveValue('');
    expect(await page.content()).not.toContain('s3cret-pw');

    // Saving another field with the password left empty keeps the stored one.
    await box.getByTestId('mqtt-host').fill('broker2.example');
    await box.getByTestId('mqtt-save').click();
    await expect(box.getByTestId('mqtt-saved')).toBeVisible();
    const kept = await (await request.get('/api/v1/config/mqtt')).json();
    expect(kept.host).toBe('broker2.example');
    expect(kept.password).toBe(cfg.password);

    // Switching to the automatic broker warns that the password is deleted.
    await box.getByTestId('mqtt-broker-supervisor').check();
    await expect(box.getByTestId('mqtt-password-delete-warn')).toBeVisible();
    await box.getByTestId('mqtt-broker-manual').check();
    // Removing the password, then back to the automatic broker.
    await box.getByTestId('mqtt-clear-password').check();
    await box.getByTestId('mqtt-save').click();
    await expect(box.getByTestId('mqtt-saved')).toBeVisible();
    expect((await (await request.get('/api/v1/config/mqtt')).json()).password).toBeNull();
    await box.getByTestId('mqtt-broker-supervisor').check();
    await box.getByTestId('mqtt-save').click();
    await expect(box.getByTestId('mqtt-saved')).toBeVisible();
    expect(await (await request.get('/api/v1/config/mqtt')).json()).toMatchObject({
      broker: 'supervisor',
      host: null,
    });
    await expectNoAxeViolations(page);
  });
});
