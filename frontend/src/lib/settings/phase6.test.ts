// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { DeviceConfig, MqttConfig, SiteConfig } from '../api/types';
import {
  applyDeviceGridEdits,
  deviceGridChanged,
  deviceGridEdit,
  hasStoredPassword,
  keepDevices,
  mqttDirty,
  mqttFormFromConfig,
  mqttFromForm,
  SECRET_MASK,
  siteWithKeep,
  toggleKeep,
} from './phase6';

const dev = (id: string, priority: number, extra: Partial<DeviceConfig> = {}): DeviceConfig =>
  ({ id, name: id, priority, para14a: false, ...extra }) as DeviceConfig;

const MQTT: MqttConfig = {
  schema_version: 1,
  enabled: true,
  broker: 'supervisor',
  port: 1883,
  tls: false,
  discovery_prefix: 'homeassistant',
  allow_mode_raise: false,
};

describe('device grid fields', () => {
  it('read the stored values with the backend defaults', () => {
    expect(deviceGridEdit(dev('a', 1))).toEqual({ para14a: false, shedRank: null });
    expect(deviceGridEdit(dev('a', 1, { para14a: true, shed_rank: 0 }))).toEqual({
      para14a: true,
      shedRank: 0,
    });
  });

  it('detect a change and apply only the edited device', () => {
    const devices = [dev('a', 1), dev('b', 2, { shed_rank: 5 })];
    expect(deviceGridChanged(devices[0]!, { para14a: false, shedRank: null })).toBe(false);
    expect(deviceGridChanged(devices[0]!, { para14a: true, shedRank: null })).toBe(true);
    const out = applyDeviceGridEdits(devices, { b: { para14a: true, shedRank: null } });
    expect(out[0]).toBe(devices[0]);
    expect(out[1]).toMatchObject({ id: 'b', para14a: true, shed_rank: null });
  });
});

describe('island keep list', () => {
  const site = {
    schema_version: 2,
    grid: {
      backup: { entity_id: 'sensor.x', island_states: ['island'], keep_devices: ['fridge'] },
    },
  } as unknown as SiteConfig;

  it('is absent without a backup entity', () => {
    expect(keepDevices(null)).toBeNull();
    const none = { schema_version: 2, grid: { backup: null } } as unknown as SiteConfig;
    expect(keepDevices(none)).toBeNull();
    expect(siteWithKeep(none, ['a'])).toBeNull();
  });

  it('toggles without duplicates and keeps unknown ids', () => {
    expect(keepDevices(site)).toEqual(['fridge']);
    expect(toggleKeep(['fridge'], 'pump', true)).toEqual(['fridge', 'pump']);
    expect(toggleKeep(['fridge', 'pump'], 'pump', true)).toEqual(['fridge', 'pump']);
    expect(toggleKeep(['fridge', 'pump'], 'fridge', false)).toEqual(['pump']);
  });

  it('writes the list into the backup section only', () => {
    const out = siteWithKeep(site, ['fridge', 'pump'])!;
    expect(out.grid.backup).toEqual({
      entity_id: 'sensor.x',
      island_states: ['island'],
      keep_devices: ['fridge', 'pump'],
    });
    expect(site.grid.backup?.keep_devices).toEqual(['fridge']);
  });
});

describe('manual MQTT broker', () => {
  const stored: MqttConfig = {
    ...MQTT,
    broker: 'manual',
    host: 'broker.lan',
    port: 8883,
    tls: true,
    username: 'sun',
    password: SECRET_MASK,
  };

  it('never copies the stored password into the form', () => {
    const form = mqttFormFromConfig(stored);
    expect(form).toMatchObject({ host: 'broker.lan', port: 8883, tls: true, username: 'sun' });
    expect(form.password).toBe('');
    expect(JSON.stringify(form)).not.toContain(SECRET_MASK);
    expect(hasStoredPassword(stored)).toBe(true);
    expect(hasStoredPassword(MQTT)).toBe(false);
  });

  it('sends the mask to keep, the typed text to replace, nothing to remove', () => {
    const form = mqttFormFromConfig(stored);
    expect(mqttFromForm(form, stored, true).password).toBe(SECRET_MASK);
    expect(mqttFromForm({ ...form, password: 'new' }, stored, true).password).toBe('new');
    expect(mqttFromForm({ ...form, clearPassword: true }, stored, true).password).toBeNull();
    expect(mqttFromForm({ ...form, password: '' }, MQTT, false).password).toBeNull();
  });

  it('trims host and user, empties become null', () => {
    const form = { ...mqttFormFromConfig(stored), host: ' h ', username: ' ', port: null };
    const body = mqttFromForm(form, stored, true);
    expect(body).toMatchObject({ host: 'h', username: null, port: 1883 });
  });

  it('drops host and credentials for the Supervisor broker, keeps the other fields', () => {
    const form = { ...mqttFormFromConfig(stored), broker: 'supervisor' as const };
    expect(mqttFromForm(form, stored, true)).toMatchObject({
      broker: 'supervisor',
      host: null,
      username: null,
      password: null,
      discovery_prefix: 'homeassistant',
      allow_mode_raise: false,
    });
  });

  it('is dirty after any edit and clean again after reverting', () => {
    const form = mqttFormFromConfig(stored);
    expect(mqttDirty(form, stored)).toBe(false);
    expect(mqttDirty({ ...form, tls: false }, stored)).toBe(true);
    expect(mqttDirty({ ...form, password: 'x' }, stored)).toBe(true);
    expect(mqttDirty({ ...form, clearPassword: true }, stored)).toBe(true);
  });
});
