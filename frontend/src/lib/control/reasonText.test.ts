// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { DevicesConfig } from '../api/types';
import { applyLanguage, loadLocale } from '../i18n/index.svelte';
import { app } from '../stores/app.svelte';
import { capitalise, tReason, withDeviceName } from './reasonText';

const reason = {
  key: 'planner.ev.grid_for_target',
  args: { device: 'wallbox', power_w: 636, price: 0.262, time: '07:00' },
};

beforeEach(() => {
  app.devices = {
    devices: [{ id: 'wallbox', name: 'Garage wallbox' }],
  } as unknown as DevicesConfig;
});
afterEach(() => {
  app.devices = null;
  applyLanguage('en');
});

describe('reason text', () => {
  it('puts the display name in place of the device id', () => {
    applyLanguage('en');
    expect(tReason(reason)).toMatch(/^Garage wallbox charges 636 W from the grid/);
  });

  it('starts with a capital when the device is not configured (the id stays)', async () => {
    app.devices = null;
    applyLanguage('en');
    expect(tReason(reason)).toMatch(/^Wallbox charges 636 W/);
    await loadLocale('de');
    applyLanguage('de');
    expect(tReason(reason).charAt(0)).toBe('W');
  });

  it('leaves other arguments and reasons without a device alone', () => {
    expect(withDeviceName({ power_w: 5 })).toEqual({ power_w: 5 });
    expect(withDeviceName(undefined)).toBeUndefined();
    expect(withDeviceName({ device: 'x', n: 1 }, (id) => id.toUpperCase())).toEqual({
      device: 'X',
      n: 1,
    });
    expect(capitalise('über', 'de')).toBe('Über');
    expect(capitalise('', 'en')).toBe('');
  });
});
