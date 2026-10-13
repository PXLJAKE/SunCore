// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { EntityCandidate, SiteConfig } from './api/types';
import {
  emptyForm,
  filterCandidates,
  formFromSite,
  previewOf,
  resolveCandidate,
  siteFromForm,
  validateForm,
} from './siteForm';

const cand = (over: Partial<EntityCandidate> = {}): EntityCandidate => ({
  entity_id: 'sensor.x',
  name: 'X',
  device_class: 'power',
  unit: 'W',
  state: '1200',
  available: true,
  ...over,
});

describe('site form round trip', () => {
  const site: SiteConfig = {
    schema_version: 2,
    grid: { para14a: false, power: { entity_id: 'sensor.grid', invert_sign: true } },
    pv: [
      {
        name: 'East',
        power: { entity_id: 'sensor.pv1', invert_sign: false },
        kwp: 4.5,
        tilt_deg: 30,
      },
      { name: 'West', power: { entity_id: 'sensor.pv2', invert_sign: false } },
    ],
    battery: {
      control: 'observe',
      power: { entity_id: 'sensor.bp', invert_sign: false },
      soc: { entity_id: 'sensor.soc', invert_sign: false },
    },
    house: null,
  };
  it('keeps everything the server sent', () => {
    const out = siteFromForm(formFromSite(site));
    expect(out.grid.power).toEqual(site.grid.power);
    expect(out.pv).toEqual(site.pv); // kwp, tilt and names survive
    expect(out.battery).toEqual(site.battery);
    expect(out.house).toBeNull();
    expect(out.schema_version).toBe(2);
  });
  it('detects an import/export pair', () => {
    const f = formFromSite({
      grid: {
        para14a: false,
        import_power: { entity_id: 'sensor.i', invert_sign: false },
        export_power: { entity_id: 'sensor.e', invert_sign: false },
      },
      pv: [],
      schema_version: 2,
    });
    expect(f.gridMode).toBe('pair');
    const out = siteFromForm(f);
    expect(out.grid.power).toBeNull();
    expect(out.grid.import_power?.entity_id).toBe('sensor.i');
  });
  it('drops empty rows and incomplete batteries', () => {
    const f = emptyForm();
    f.batteryEnabled = true;
    f.batteryPower.entity_id = 'sensor.bp'; // SoC missing
    f.pv.push({ entity_id: ' sensor.pv ', invert_sign: false });
    const out = siteFromForm(f);
    expect(out.pv).toEqual([
      { name: 'PV 2', power: { entity_id: 'sensor.pv', invert_sign: false } },
    ]);
    expect(out.battery).toBeNull();
  });
  it('starts empty without a stored config', () => {
    expect(formFromSite(null).pv).toHaveLength(1);
  });
});

describe('validateForm', () => {
  it('accepts complete and fully empty groups', () => {
    expect(validateForm(emptyForm())).toEqual([]);
    const f = emptyForm();
    f.batteryEnabled = true;
    f.batteryPower.entity_id = 'sensor.p';
    f.batterySoc.entity_id = 'sensor.s';
    f.gridMode = 'pair';
    f.gridImport.entity_id = 'sensor.i';
    f.gridExport.entity_id = 'sensor.e';
    expect(validateForm(f)).toEqual([]);
  });
  it('flags an incomplete battery instead of dropping it', () => {
    const f = emptyForm();
    f.batteryEnabled = true;
    f.batteryPower.entity_id = 'sensor.p';
    expect(validateForm(f)).toEqual(['settings.error.battery_incomplete']);
    f.batteryPower.entity_id = '';
    f.batterySoc.entity_id = 'sensor.s';
    expect(validateForm(f)).toEqual(['settings.error.battery_incomplete']);
    f.batteryEnabled = false; // disabled battery is simply not sent
    expect(validateForm(f)).toEqual([]);
  });
  it('flags a grid pair with one sensor only', () => {
    const f = emptyForm();
    f.gridMode = 'pair';
    f.gridImport.entity_id = 'sensor.i';
    expect(validateForm(f)).toEqual(['settings.error.grid_pair_incomplete']);
  });
});

describe('previewOf', () => {
  it('applies the sign switch', () => {
    expect(previewOf(cand(), false, 'power')).toEqual({ type: 'power', watts: 1200 });
    expect(previewOf(cand(), true, 'power')).toEqual({ type: 'power', watts: -1200 });
  });
  it('converts kW and refuses to guess other units', () => {
    expect(previewOf(cand({ unit: 'kW', state: '1.5' }), false, 'power')).toEqual({
      type: 'power',
      watts: 1500,
    });
    expect(previewOf(cand({ unit: 'A', state: '3' }), false, 'power')).toEqual({
      type: 'raw',
      text: '3 A',
    });
  });
  it('handles missing, unavailable and non numeric entities', () => {
    expect(previewOf(undefined, false, 'power')).toEqual({ type: 'none' });
    expect(previewOf(cand({ available: false }), false, 'power')).toEqual({ type: 'unavailable' });
    expect(previewOf(cand({ state: 'unknown' }), false, 'power')).toEqual({ type: 'unavailable' });
  });
  it('shows SoC in percent', () => {
    expect(previewOf(cand({ unit: '%', state: '64' }), false, 'soc')).toEqual({
      type: 'percent',
      percent: 64,
    });
  });
});

describe('entity picker matching', () => {
  const soc = cand({
    entity_id: 'sensor.kl_strom_plenticore_battery_soc',
    name: 'Plenticore Battery SoC',
    device_class: 'battery',
    unit: '%',
    state: '75',
  });
  const grid = cand({ entity_id: 'sensor.grid_power', name: 'Grid Power', unit: 'W' });
  const pv = cand({ entity_id: 'sensor.pv_kw', name: 'PV Leistung', unit: 'kW' });
  const temp = cand({ entity_id: 'sensor.temp', name: 'Temperatur', unit: '°C' });
  const all = [soc, grid, pv, temp];

  it('filters by kind', () => {
    expect(filterCandidates(all, 'power', '').items).toEqual([grid, pv]);
    expect(filterCandidates(all, 'soc', '').items).toEqual([soc]);
  });
  it('matches entity id and friendly name case-insensitively', () => {
    expect(filterCandidates(all, 'soc', 'PLENTICORE').items).toEqual([soc]);
    expect(filterCandidates(all, 'soc', 'kl_strom').items).toEqual([soc]);
    expect(filterCandidates(all, 'power', 'leistung').items).toEqual([pv]);
    expect(filterCandidates(all, 'power', 'nothing').total).toBe(0);
  });
  it('requires all whitespace-separated terms', () => {
    expect(filterCandidates(all, 'soc', 'battery  soc').items).toEqual([soc]);
    expect(filterCandidates(all, 'soc', 'battery grid').items).toEqual([]);
  });
  it('does not offer the SoC sensor for power fields', () => {
    expect(filterCandidates(all, 'power', 'battery').items).toEqual([]);
  });
  it('caps the list but reports the total', () => {
    const many = Array.from({ length: 80 }, (_, i) => cand({ entity_id: `sensor.p${i}` }));
    const r = filterCandidates(many, 'power', 'p');
    expect(r.items).toHaveLength(50);
    expect(r.total).toBe(80);
    expect(filterCandidates(many, 'power', '', 5).items).toHaveLength(5);
  });
  it('resolves an entity id or an exact friendly name', () => {
    expect(resolveCandidate(all, ' sensor.grid_power ')).toBe(grid);
    expect(resolveCandidate(all, 'plenticore battery soc')).toBe(soc);
    expect(resolveCandidate(all, 'Plenticore')).toBeUndefined();
    expect(resolveCandidate(all, '')).toBeUndefined();
  });
});
