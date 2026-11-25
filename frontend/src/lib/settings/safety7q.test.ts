// SPDX-License-Identifier: Apache-2.0
// Safety review of 7-Q: F1 (one lock, latest copy) and F3 (stale keep ids).
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { MqttConfig, SiteConfig } from '../api/types';

const store: { mqtt: MqttConfig | null; site: SiteConfig; devices: unknown } = vi.hoisted(() => ({
  mqtt: null,
  site: null as never,
  devices: null,
}));
const gate: { release: (() => void) | null } = vi.hoisted(() => ({ release: null }));

vi.mock('../api/client', () => ({
  api: {
    getSection: vi.fn(async (name: string) => (name === 'mqtt' ? store.mqtt : store.devices)),
    putSection: vi.fn(async (name: string, body: unknown) => {
      if (name === 'mqtt') store.mqtt = body as MqttConfig;
      else store.devices = body;
      return body;
    }),
    putSite: vi.fn(async (site: SiteConfig) => {
      store.site = site;
      return site;
    }),
    getSite: vi.fn(async () => store.site),
  },
}));

import { api } from '../api/client';
import {
  applyDeviceGridEdits,
  mergeKeep,
  mqttFromForm,
  mqttFormFromConfig,
  pruneKeep,
} from './phase6';
import { mqttLock } from './mqttSave.svelte';

const MQTT: MqttConfig = {
  schema_version: 1,
  enabled: true,
  broker: 'manual',
  host: 'old.example',
  port: 1883,
  tls: false,
  discovery_prefix: 'homeassistant',
  allow_mode_raise: true,
};

beforeEach(() => {
  store.mqtt = { ...MQTT };
  gate.release = null;
  vi.clearAllMocks();
});

describe('F1: one lock for the broker and the mode-raise save', () => {
  it('builds the broker body from the latest copy, so a raise untick is not undone', async () => {
    // The broker form was opened while allow_mode_raise was true.
    const staleForm = { ...mqttFormFromConfig(MQTT), host: 'new.example' };
    // Meanwhile the user unticked the opt-in and it was stored.
    store.mqtt = { ...MQTT, allow_mode_raise: false };
    const saved = await mqttLock.save((latest) => mqttFromForm(staleForm, latest, false));
    expect(saved).toMatchObject({ host: 'new.example', allow_mode_raise: false });
    expect(store.mqtt?.allow_mode_raise).toBe(false);
  });

  it('refuses a second save while one runs and releases the lock afterwards', async () => {
    let open!: () => void;
    const blocker = new Promise<void>((r) => (open = r));
    vi.mocked(api.getSection).mockImplementationOnce(async () => {
      await blocker;
      return store.mqtt;
    });
    const first = mqttLock.save((l) => ({ ...l, allow_mode_raise: false }));
    expect(mqttLock.busy).toBe(true);
    const second = await mqttLock.save((l) => ({ ...l, host: 'x.example' }));
    expect(second).toBeNull();
    expect(api.putSection).not.toHaveBeenCalled();
    open();
    await first;
    expect(mqttLock.busy).toBe(false);
    expect(store.mqtt).toMatchObject({ allow_mode_raise: false, host: 'old.example' });
  });

  it('releases the lock when the write fails', async () => {
    vi.mocked(api.putSection).mockRejectedValueOnce(new Error('boom'));
    await expect(mqttLock.save((l) => l)).rejects.toThrow('boom');
    expect(mqttLock.busy).toBe(false);
  });
});

describe('F3: a deleted device leaves the keep list for good', () => {
  const site = {
    schema_version: 2,
    grid: {
      backup: { entity_id: 'sensor.x', island_states: ['island'], keep_devices: ['pump', 'rod'] },
    },
  } as unknown as SiteConfig;

  it('drops ids that are gone or were stale, keeps the rest', () => {
    const out = pruneKeep(site, ['pump', 'rod'], ['rod'])!;
    expect(out.grid.backup?.keep_devices).toEqual(['rod']);
    // Already stale before the change: dropped before a new device can reuse the id.
    expect(pruneKeep(site, ['rod'], ['pump', 'rod'])!.grid.backup?.keep_devices).toEqual(['rod']);
    expect(pruneKeep(site, ['pump', 'rod'], ['pump', 'rod'])).toBeNull();
    expect(pruneKeep(null, [], [])).toBeNull();
  });

  it('delete then add with the same id: the new device is not kept', async () => {
    const { wizard } = await import('../stores/wizard.svelte');
    const dev = (id: string) => ({ id, name: id, priority: 1 }) as never;
    wizard.saved = {
      ...wizard.saved,
      site,
      devices: { schema_version: 1, devices: [dev('pump'), dev('rod')] },
    } as never;
    store.site = site;
    store.devices = { schema_version: 1, devices: [dev('pump'), dev('rod')] };
    // Delete "pump" ...
    await wizard.saveDevices((cur) => cur.filter((d) => d.id !== 'pump'));
    expect(store.site.grid.backup?.keep_devices).toEqual(['rod']);
    // ... and add a new device with the same id.
    await wizard.saveDevices((cur) => [...cur, dev('pump')]);
    expect(store.site.grid.backup?.keep_devices).toEqual(['rod']);
  });
});

describe('7-Q: whole-section saves start from the server copy', () => {
  const site = {
    schema_version: 2,
    grid: { backup: { entity_id: 'sensor.x', island_states: ['island'], keep_devices: ['a'] } },
  } as unknown as SiteConfig;
  const dev = (id: string, extra = {}) => ({ id, name: id, priority: 1, ...extra }) as never;

  it('a keep-list save keeps an entry another tab added meanwhile', () => {
    // The panel saw ["a"], the user ticked "b"; another tab stored ["a", "c"] in between.
    expect(mergeKeep(['a', 'c'], ['a'], ['a', 'b'])).toEqual(['a', 'c', 'b']);
    // An untick removes only that id from the server list.
    expect(mergeKeep(['a', 'c'], ['a'], [])).toEqual(['c']);
  });

  it('a shed_rank save keeps a device value changed on the server after load', async () => {
    const { wizard } = await import('../stores/wizard.svelte');
    wizard.saved = {
      ...wizard.saved,
      site,
      devices: { schema_version: 1, devices: [dev('a'), dev('b')] },
    } as never;
    store.site = site;
    // After load another tab set para14a on "b" and renamed "a".
    store.devices = {
      schema_version: 1,
      devices: [dev('a', { name: 'renamed' }), dev('b', { para14a: true })],
    };
    await wizard.saveDevices((cur) =>
      applyDeviceGridEdits(cur, { a: { para14a: false, shedRank: 3 } }),
    );
    const out = (store.devices as { devices: Record<string, unknown>[] }).devices;
    expect(out[0]).toMatchObject({ id: 'a', name: 'renamed', shed_rank: 3 });
    expect(out[1]).toMatchObject({ id: 'b', para14a: true });
  });

  it('pruning reads the server site, so a keep entry stored meanwhile is not overwritten', async () => {
    const { wizard } = await import('../stores/wizard.svelte');
    wizard.saved = {
      ...wizard.saved,
      site,
      devices: { schema_version: 1, devices: [dev('a')] },
    } as never;
    store.devices = { schema_version: 1, devices: [dev('a'), dev('z')] };
    store.site = {
      ...site,
      grid: { backup: { ...site.grid.backup!, keep_devices: ['a', 'z'] } },
    } as SiteConfig;
    await wizard.saveDevices((cur) => cur.filter((d) => d.id !== 'a'));
    expect(store.site.grid.backup?.keep_devices).toEqual(['z']);
  });
});
