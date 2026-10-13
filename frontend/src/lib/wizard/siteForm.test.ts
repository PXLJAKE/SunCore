// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { SiteConfig, Suggestion } from '../api/types';
import {
  applySuggestions,
  emptySiteForm,
  isSiteSavable,
  siteFormFromSite,
  siteFromForm,
  validateSiteForm,
  visibleSuggestions,
} from './siteForm';

const ref = (entity_id: string, invert_sign = false) => ({ entity_id, invert_sign });
const SITE: SiteConfig = {
  schema_version: 2,
  grid: {
    power: ref('sensor.grid', true),
    import_power: null,
    export_power: null,
    import_limit_w: 11000,
    export_limit_w: null,
    export_limit_pct_kwp: 70,
    para14a: true,
    para14a_signal: ref('binary_sensor.evu'),
    energy_import: ref('sensor.imp_kwh'),
    energy_export: null,
  },
  pv: [
    {
      name: 'Roof',
      power: ref('sensor.pv'),
      kwp: 9.8,
      azimuth_deg: 180,
      tilt_deg: 30,
      inverter: 'A',
    },
  ],
  battery: {
    power: ref('sensor.bat'),
    soc: ref('sensor.soc'),
    capacity_wh: 10000,
    min_soc_pct: 10,
    max_soc_pct: 95,
    max_charge_w: 5000,
    max_discharge_w: 5000,
    control: 'observe',
  },
  house: ref('sensor.house'),
};

describe('site form mapping', () => {
  it('round-trips a complete site', () => {
    expect(siteFromForm(siteFormFromSite(SITE))).toEqual(SITE);
  });
  it('shows the capacity in kWh and stores Wh', () => {
    const form = siteFormFromSite(SITE);
    expect(form.battery.capacityKwh).toBe(10);
    form.battery.capacityKwh = 13.5;
    expect(siteFromForm(form).battery?.capacity_wh).toBe(13500);
  });
  it('detects the pair variant and keeps only the chosen variant on save', () => {
    const form = siteFormFromSite({
      ...SITE,
      grid: {
        ...SITE.grid,
        power: null,
        import_power: ref('sensor.i'),
        export_power: ref('sensor.e'),
      },
    });
    expect(form.grid.mode).toBe('pair');
    form.grid.mode = 'single';
    form.grid.power = ref('sensor.s');
    const grid = siteFromForm(form).grid;
    expect(grid.power?.entity_id).toBe('sensor.s');
    expect(grid.import_power).toBeNull();
    expect(grid.export_power).toBeNull();
  });
  it('sends only the selected export limit and the signal only with §14a', () => {
    const form = siteFormFromSite(SITE);
    form.grid.exportLimitMode = 'w';
    form.grid.exportLimitW = 5000;
    form.grid.para14a = false;
    const grid = siteFromForm(form).grid;
    expect(grid.export_limit_w).toBe(5000);
    expect(grid.export_limit_pct_kwp).toBeNull();
    expect(grid.para14a_signal).toBeNull();
  });
  it('never inverts the state of charge or counters', () => {
    const form = siteFormFromSite(SITE);
    form.battery.soc.invert_sign = true;
    form.grid.energyImport.invert_sign = true;
    const site = siteFromForm(form);
    expect(site.battery?.soc.invert_sign).toBe(false);
    expect(site.grid.energy_import?.invert_sign).toBe(false);
  });
  it('drops empty PV rows, names unnamed ones and de-duplicates names', () => {
    const form = emptySiteForm();
    form.pv = [
      { name: '', power: ref('sensor.a'), kwp: null, azimuth: null, tilt: null, inverter: '' },
      { name: '', power: ref(''), kwp: null, azimuth: null, tilt: null, inverter: '' },
      { name: 'PV 1', power: ref('sensor.b'), kwp: null, azimuth: null, tilt: null, inverter: '' },
    ];
    expect((siteFromForm(form).pv ?? []).map((a) => a.name)).toEqual(['PV 1', 'PV 1 2']);
  });
  it('omits a battery that is switched off or incomplete', () => {
    const form = siteFormFromSite(SITE);
    form.battery.enabled = false;
    expect(siteFromForm(form).battery).toBeNull();
    form.battery.enabled = true;
    form.battery.soc.entity_id = '';
    expect(siteFromForm(form).battery).toBeNull();
    expect(validateSiteForm(form)).toContain('battery_incomplete');
  });
  it('knows when a site can be stored', () => {
    const form = emptySiteForm();
    expect(isSiteSavable(form)).toBe(false);
    expect(validateSiteForm(form)).toContain('grid_missing');
    form.grid.mode = 'pair';
    form.grid.importPower = ref('sensor.i');
    expect(validateSiteForm(form)).toContain('grid_pair_incomplete');
    form.grid.exportPower = ref('sensor.e');
    expect(isSiteSavable(form)).toBe(true);
  });
});

describe('energy suggestions', () => {
  const sug = (path: string, value: unknown): Suggestion => ({
    path,
    value: value as Suggestion['value'],
    reason: { key: 'wizard.energy.reason.grid_rate', args: {} },
  });
  const all = [
    sug('grid.power', ref('sensor.g')),
    sug('grid.energy_import', ref('sensor.gi')),
    sug('pv.0.name', 'PV 1'),
    sug('pv.0.power', ref('sensor.p')),
    sug('battery.power', ref('sensor.b', true)),
    sug('battery.soc', ref('sensor.s')),
    sug('battery.capacity_wh', 10000),
    sug('something.else', ref('sensor.x')),
  ];
  it('hides the name suggestions that belong to an array', () => {
    expect(visibleSuggestions(all).map((s) => s.path)).not.toContain('pv.0.name');
  });
  it('applies only the picked suggestions', () => {
    const form = emptySiteForm();
    const n = applySuggestions(form, all, new Set(['grid.power', 'battery.soc']));
    expect(n).toBe(2);
    expect(form.grid.power.entity_id).toBe('sensor.g');
    expect(form.grid.energyImport.entity_id).toBe('');
    expect(form.battery.enabled).toBe(true);
    expect(form.battery.power.entity_id).toBe('');
  });
  it('takes the array name along with its power sensor', () => {
    const form = emptySiteForm();
    applySuggestions(form, all, new Set(['pv.0.power']));
    expect(form.pv[0]!.name).toBe('PV 1');
    expect(form.pv[0]!.power.entity_id).toBe('sensor.p');
  });
  it('applies inversion, capacity and ignores unknown paths', () => {
    const form = emptySiteForm();
    const picked = new Set(all.map((s) => s.path));
    expect(applySuggestions(form, all, picked)).toBe(7);
    expect(form.battery.power).toEqual(ref('sensor.b', true));
    expect(form.battery.capacityKwh).toBe(10);
    expect(isSiteSavable(form)).toBe(true);
  });
});
