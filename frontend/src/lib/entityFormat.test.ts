// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { EntityCandidate } from './api/types';
import {
  candidateValueText,
  criterionKeys,
  filterCandidates,
  fragmentFor,
  matchesQuery,
  measuring,
  resolveCandidate,
} from './entityFormat';

const cand = (over: Partial<EntityCandidate> = {}): EntityCandidate => ({
  entity_id: 'sensor.sim_battery_power',
  name: 'Sim battery power',
  device_class: 'power',
  unit: 'W',
  state: '4246.6',
  available: true,
  domain: 'sensor',
  platform: 'suncore_sim',
  state_class: 'measurement',
  area_id: null,
  area_name: 'Cellar',
  device_id: 'sim_house',
  device_name: 'Demo house',
  score: 75,
  criteria: ['detect.criterion.domain', 'detect.criterion.translation_key'],
  value: 4246.6,
  quality: 'ok',
  ...over,
});

const text = (c: EntityCandidate): string => candidateValueText(c).replace(/\u00a0/g, ' ');

describe('candidateValueText', () => {
  it('formats power, kilowatts, percent and other units', () => {
    expect(text(cand())).toBe('4.25 kW');
    expect(text(cand({ value: 800, unit: 'W' }))).toBe('800 W');
    expect(text(cand({ value: 1.5, unit: 'kW' }))).toBe('1.5 kW');
    expect(text(cand({ value: 76.3, unit: '%' }))).toBe('76 %');
    expect(text(cand({ value: 12.5, unit: 'kWh' }))).toBe('12.5 kWh');
  });
  it('shows raw states of non-numeric entities and unavailable ones', () => {
    expect(text(cand({ value: null, unit: '', state: 'off' }))).toBe('off');
    expect(text(cand({ value: null, state: 'unavailable', available: false }))).toBe('unavailable');
  });
});

describe('search', () => {
  const list = [
    cand(),
    cand({ entity_id: 'sensor.grid', name: 'Grid meter', area_name: null, device_name: 'Meter' }),
  ];
  it('matches name, entity id, area and device terms', () => {
    expect(matchesQuery(list[0]!, 'battery power')).toBe(true);
    expect(matchesQuery(list[0]!, 'cellar')).toBe(true);
    expect(matchesQuery(list[0]!, 'sim_battery')).toBe(true);
    expect(matchesQuery(list[0]!, 'grid')).toBe(false);
    expect(filterCandidates(list, '').total).toBe(2);
    expect(filterCandidates(list, 'meter').items.map((c) => c.entity_id)).toEqual(['sensor.grid']);
  });
  it('caps the rendered options but reports the total', () => {
    const many = Array.from({ length: 80 }, (_, i) => cand({ entity_id: `sensor.x_${i}` }));
    const r = filterCandidates(many, '');
    expect(r.items).toHaveLength(50);
    expect(r.total).toBe(80);
  });
  it('resolves entity ids and exact names', () => {
    expect(resolveCandidate(list, 'SENSOR.GRID')?.name).toBe('Grid meter');
    expect(resolveCandidate(list, 'grid meter')?.entity_id).toBe('sensor.grid');
    expect(resolveCandidate(list, 'nothing')).toBeUndefined();
    expect(resolveCandidate(list, '  ')).toBeUndefined();
  });
});

describe('criterionKeys', () => {
  it('collapses exact and pattern forms of one criterion', () => {
    expect(
      criterionKeys({
        criteria: [
          'detect.criterion.domain',
          'detect.criterion.translation_key',
          'detect.criterion.translation_key_pattern',
        ],
      }),
    ).toEqual(['detect.criterion.domain', 'detect.criterion.translation_key']);
  });
});

describe('fragmentFor', () => {
  const ref = { entity_id: 'sensor.a', invert_sign: true };
  it('nests the path into a document fragment', () => {
    expect(fragmentFor('battery.power', ref)).toEqual({ battery: { power: ref } });
    expect(fragmentFor('grid.energy_import', ref)).toEqual({ grid: { energy_import: ref } });
    expect(fragmentFor('house', ref)).toEqual({ house: ref });
  });
  it('builds lists for numeric segments', () => {
    const f = fragmentFor('pv.1.power', ref) as { pv: unknown[] };
    expect(f.pv[1]).toEqual({ power: ref });
    expect(f.pv).toHaveLength(2);
  });
});

describe('measuring', () => {
  it('treats sensors and numbers as measuring, climate and switch as raw states', () => {
    expect(measuring({ entity_id: 'sensor.x_power', domain: 'sensor' })).toBe(true);
    expect(measuring({ entity_id: 'number.limit', domain: '' })).toBe(true);
    expect(measuring({ entity_id: 'climate.ac', domain: 'climate' })).toBe(false);
    expect(measuring({ entity_id: 'switch.plug', domain: '' })).toBe(false);
  });
});
