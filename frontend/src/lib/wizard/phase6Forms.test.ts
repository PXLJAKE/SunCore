// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { DeviceTemplate, SiteConfig } from '../api/types';
import {
  deviceFromForm,
  needsBehaviourConfirmation,
  newDeviceForm,
  programPeakW,
} from './deviceForm';
import { parseIslandStates, siteFormFromSite, siteFromForm } from './siteForm';

const ref = (entity_id: string, invert_sign = false) => ({ entity_id, invert_sign });

const SITE: SiteConfig = {
  schema_version: 2,
  grid: {
    power: ref('sensor.grid'),
    import_power: null,
    export_power: null,
    import_limit_w: null,
    export_limit_w: null,
    export_limit_pct_kwp: null,
    para14a: false,
    para14a_signal: null,
    energy_import: null,
    energy_export: null,
    peak_shaving: { enabled: false, peak_floor_w: 2500, capacity_price_per_kw_month: 0 },
    export_dynamic: false,
    zero_export: false,
    para14a_limit_w: 4200,
    para14a_signal_kind: 'binary',
    backup: null,
  },
  pv: [],
  battery: null,
  house: null,
};

describe('grid chain form', () => {
  it('edits peak shaving, export chain, §14a and the backup entity', () => {
    const form = siteFormFromSite(SITE);
    form.grid.peakEnabled = true;
    form.grid.peakFloorW = 3000;
    form.grid.peakPrice = 4.5;
    form.grid.zeroExport = true;
    form.grid.exportDynamic = true;
    form.grid.para14a = true;
    form.grid.para14aSignalKind = 'power_w';
    form.grid.para14aLimitW = 3600;
    form.grid.backupEnabled = true;
    form.grid.backup = ref('sensor.backup_mode');
    form.grid.backupStates = 'island, backup ,';
    const grid = siteFromForm(form).grid;
    expect(grid.peak_shaving).toEqual({
      enabled: true,
      peak_floor_w: 3000,
      capacity_price_per_kw_month: 4.5,
    });
    expect(grid.zero_export).toBe(true);
    expect(grid.export_dynamic).toBe(true);
    expect(grid.para14a_signal_kind).toBe('power_w');
    expect(grid.para14a_limit_w).toBe(3600);
    expect(grid.backup).toEqual({
      entity_id: 'sensor.backup_mode',
      island_states: ['island', 'backup'],
      keep_devices: [],
    });
  });

  it('keeps the devices a backup keeps running and drops a backup without states', () => {
    const site: SiteConfig = {
      ...SITE,
      grid: {
        ...SITE.grid,
        backup: { entity_id: 'sensor.b', island_states: ['on'], keep_devices: ['fridge'] },
      },
    };
    const form = siteFormFromSite(site);
    expect(form.grid.backupEnabled).toBe(true);
    expect(form.grid.backupStates).toBe('on');
    expect(siteFromForm(form).grid.backup?.keep_devices).toEqual(['fridge']);
    form.grid.backupStates = ' , ';
    expect(siteFromForm(form).grid.backup).toBeNull();
    form.grid.backupStates = 'on';
    form.grid.backupEnabled = false;
    expect(siteFromForm(form).grid.backup).toBeNull();
  });

  it('splits the island states', () => {
    expect(parseIslandStates('a, b ,,c')).toEqual(['a', 'b', 'c']);
    expect(parseIslandStates('')).toEqual([]);
  });
});

const template = (defaults: Record<string, unknown>, device_kind = 'wallbox'): DeviceTemplate =>
  ({
    id: 'generic.test',
    name: 'x',
    device_kind,
    roles: {},
    defaults: {
      power_model: { kind: 'nominal', nominal_w: 2000 },
      min_run_s: 0,
      min_pause_s: 0,
      ...defaults,
    },
  }) as unknown as DeviceTemplate;

describe('Phase 6 device form', () => {
  it('carries the EV, program and thermal specs from the template into the device', () => {
    const ev = { default_mode: 'pv', efficiency: 0.9 };
    const program = { runtime_min: 120, idle_w: 5, start_method: 'plug', profile_wh: [] };
    const t = template({
      control: { kind: 'charger', role: 'current' },
      ev,
      program,
      thermal: { grid_preheat: false },
    });
    const form = newDeviceForm(t, 'Garage', 1);
    expect(form.ev?.capacity_wh).toBeNull();
    const dev = deviceFromForm(form, 'garage', t);
    expect(dev.ev).toMatchObject(ev);
    expect(dev.program).toMatchObject(program);
    expect(dev.thermal).toEqual({ grid_preheat: false });
    expect(dev.behaviour_confirmed).toBe(false);
  });

  it('stores the confirmation the user gives', () => {
    const t = template({ control: { kind: 'charger', role: 'current' } });
    const form = newDeviceForm(t, 'Garage', 1);
    form.behaviourConfirmed = true;
    expect(deviceFromForm(form, 'garage', t).behaviour_confirmed).toBe(true);
  });

  it('asks for confirmation exactly where the backend caps the mode', () => {
    const f = (control: object, program: object | null = null, kind = 'generic') =>
      needsBehaviourConfirmation({ control, program, kind } as never);
    expect(f({ kind: 'on_off' })).toBe(false);
    expect(f({ kind: 'charger' })).toBe(true);
    expect(f({ kind: 'loadpoint' })).toBe(true);
    expect(f({ kind: 'sg_ready' })).toBe(true);
    expect(f({ kind: 'on_off' }, { runtime_min: 60 })).toBe(true);
    expect(f({ kind: 'setpoint' }, null, 'heat_pump')).toBe(true);
    expect(f({ kind: 'setpoint' }, null, 'climate')).toBe(false);
  });

  it('derives the peak of a program from its profile', () => {
    expect(programPeakW({ profile_wh: [100, 500, 250] })).toBe(2000);
    expect(programPeakW({ profile_wh: [] })).toBeNull();
  });
});
