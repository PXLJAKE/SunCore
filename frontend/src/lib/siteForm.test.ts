// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { EntityCandidate, SiteConfig } from './api/types';
import { emptyForm, formFromSite, previewOf, siteFromForm } from './siteForm';

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
    schema_version: 1,
    grid: { power: { entity_id: 'sensor.grid', invert_sign: true } },
    pv: [
      { entity_id: 'sensor.pv1', invert_sign: false },
      { entity_id: 'sensor.pv2', invert_sign: false },
    ],
    battery: {
      power: { entity_id: 'sensor.bp', invert_sign: false },
      soc: { entity_id: 'sensor.soc', invert_sign: false },
    },
    house: null,
  };
  it('keeps everything the server sent', () => {
    const out = siteFromForm(formFromSite(site));
    expect(out.grid.power).toEqual(site.grid.power);
    expect(out.pv).toEqual(site.pv);
    expect(out.battery).toEqual(site.battery);
    expect(out.house).toBeNull();
    expect(out.schema_version).toBe(1);
  });
  it('detects an import/export pair', () => {
    const f = formFromSite({
      grid: {
        import_power: { entity_id: 'sensor.i', invert_sign: false },
        export_power: { entity_id: 'sensor.e', invert_sign: false },
      },
      pv: [],
      schema_version: 1,
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
    expect(out.pv).toEqual([{ entity_id: 'sensor.pv', invert_sign: false }]);
    expect(out.battery).toBeNull();
  });
  it('starts empty without a stored config', () => {
    expect(formFromSite(null).pv).toHaveLength(1);
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
