// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { STEP_IDS } from '../wizard/steps';
import { PAGE_STEPS, pageRows, stepSummary, type Saved } from './summary';

const empty: Saved = {
  site: null,
  region: null,
  tariff: null,
  forecast: null,
  battery: null,
  devices: null,
  goals: null,
  operation: null,
  wizard: null,
};

describe('settings summaries', () => {
  it('spread the ten steps over the pages, each once and in order', () => {
    const all = Object.values(PAGE_STEPS).flatMap((steps) => [...(steps ?? [])]);
    expect(all).toEqual([...STEP_IDS]);
  });

  it('say "Not set up" for missing sections and keep the defaults readable', () => {
    expect(stepSummary('grid', empty)).toBe('Not set up');
    expect(stepSummary('mode', empty)).toBe('Shadow mode (default)');
    expect(stepSummary('devices', empty)).toBe('No devices');
  });

  it('summarise a stored site', () => {
    const saved = {
      ...empty,
      site: {
        schema_version: 1,
        grid: { power: { entity_id: 'sensor.grid', invert_sign: false } },
        pv: [{ name: 'Roof', kwp: 8 }],
        battery: null,
      },
      forecast: { sources: [{}, {}] },
    } as unknown as Saved;
    const rows = pageRows('system', saved);
    expect(rows.map((r) => r.step)).toEqual([
      'region',
      'energy',
      'grid',
      'pv',
      'battery',
      'tariff',
    ]);
    expect(rows.find((r) => r.step === 'grid')!.text).toBe('One signed sensor: sensor.grid');
    expect(rows.find((r) => r.step === 'pv')!.text).toBe(
      '1 solar array, 8 kWp · 2 forecast sources',
    );
    expect(rows.find((r) => r.step === 'battery')!.text).toBe('No battery');
  });
});
