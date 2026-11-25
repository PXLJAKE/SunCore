// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { DeviceConfig } from '../api/types';
import { device } from '../control/fixtures';
import {
  defaultDeadline,
  evPlug,
  evTarget,
  flexKind,
  jobOpen,
  liftK,
  parseDeadline,
  sgState,
  startsOnRelease,
  targetToForm,
  thermalView,
  toLocalInput,
} from './present';

const NOW = new Date(2026, 5, 21, 9, 30);

const config = (over: Record<string, unknown>): DeviceConfig =>
  ({ id: 'x', kind: 'generic', control: { kind: 'on_off', role: 'sw' }, ...over }) as DeviceConfig;

describe('flexKind', () => {
  it('tells chargers, programs and heat pumps apart', () => {
    expect(flexKind(undefined)).toBeNull();
    expect(flexKind(config({}))).toBeNull();
    expect(flexKind(config({ control: { kind: 'charger' } }))).toBe('ev');
    expect(flexKind(config({ control: { kind: 'loadpoint' } }))).toBe('ev');
    expect(flexKind(config({ control: { kind: 'sg_ready' } }))).toBe('heat_pump');
    expect(flexKind(config({ kind: 'heat_pump', control: { kind: 'setpoint' } }))).toBe(
      'heat_pump',
    );
    expect(flexKind(config({ kind: 'climate', control: { kind: 'setpoint' } }))).toBeNull();
    expect(flexKind(config({ program: { start_method: 'plug' } }))).toBe('program');
  });

  it('warns only for a plug-started program', () => {
    expect(startsOnRelease(config({ program: { start_method: 'plug' } }))).toBe(true);
    expect(startsOnRelease(config({ program: { start_method: 'remote' } }))).toBe(false);
    expect(startsOnRelease(config({}))).toBe(false);
  });
});

describe('deadlines', () => {
  it('formats a local input value and suggests the next 07:00 at least 2 h away', () => {
    expect(toLocalInput(new Date(2026, 0, 5, 7, 3))).toBe('2026-01-05T07:03');
    expect(toLocalInput(defaultDeadline(new Date(2026, 5, 21, 5, 30)))).toBe('2026-06-22T07:00');
    expect(toLocalInput(defaultDeadline(NOW))).toBe('2026-06-22T07:00');
    expect(toLocalInput(defaultDeadline(new Date(2026, 5, 21, 3, 0)))).toBe('2026-06-21T07:00');
  });

  it('parses a future local time and rejects garbage and the past', () => {
    const ok = parseDeadline('2026-06-22T07:00', NOW);
    expect(ok.ok && ok.iso).toBe(new Date(2026, 5, 22, 7, 0).toISOString());
    expect(parseDeadline('', NOW)).toEqual({ ok: false, error: 'invalid' });
    expect(parseDeadline('tomorrow', NOW)).toEqual({ ok: false, error: 'invalid' });
    expect(parseDeadline('2026-06-21T09:00', NOW)).toEqual({ ok: false, error: 'past' });
  });
});

describe('evTarget', () => {
  it('builds a SoC target', () => {
    const r = evTarget('soc', 80, '2026-06-22T07:00', NOW);
    expect(r.ok && r.target).toEqual({
      soc_pct: 80,
      energy_wh: null,
      by: new Date(2026, 5, 22, 7, 0).toISOString(),
    });
  });

  it('sends the energy in Wh', () => {
    const r = evTarget('energy', 12.5, '2026-06-22T07:00', NOW);
    expect(r.ok && r.target.energy_wh).toBe(12_500);
    expect(r.ok && r.target.soc_pct).toBeNull();
  });

  it('rejects out-of-range values and a past time', () => {
    expect(evTarget('soc', 101, '2026-06-22T07:00', NOW)).toEqual({ ok: false, error: 'value' });
    expect(evTarget('soc', 0, '2026-06-22T07:00', NOW)).toEqual({ ok: false, error: 'value' });
    expect(evTarget('soc', null, '2026-06-22T07:00', NOW)).toEqual({ ok: false, error: 'value' });
    expect(evTarget('energy', 501, '2026-06-22T07:00', NOW)).toEqual({ ok: false, error: 'value' });
    expect(evTarget('soc', 80, '2026-06-20T07:00', NOW)).toEqual({ ok: false, error: 'past' });
  });

  it('puts a stored target back into the form', () => {
    expect(targetToForm(null)).toBeNull();
    const by = new Date(2026, 5, 22, 7, 0).toISOString();
    expect(targetToForm({ soc_pct: 70, energy_wh: null, by })).toEqual({
      kind: 'soc',
      value: 70,
      by: '2026-06-22T07:00',
    });
    expect(targetToForm({ soc_pct: null, energy_wh: 9000, by })?.value).toBe(9);
  });
});

describe('device state helpers', () => {
  it('reads the plug state', () => {
    expect(evPlug(null)).toBe('unknown');
    expect(evPlug(device({ state: { quality: 'ok' } }).state)).toBe('unknown');
    expect(evPlug(device({ state: { quality: 'ok', connected: true } }).state)).toBe('plugged');
    expect(evPlug(device({ state: { quality: 'ok', connected: false } }).state)).toBe('unplugged');
  });

  it('reads the SG-Ready state and the lift', () => {
    expect(sgState(2)).toBe(2);
    expect(sgState(7)).toBeNull();
    expect(sgState(null)).toBeNull();
    expect(liftK(device())).toBe(-1);
    expect(liftK(device({ base_setpoint_c: null }))).toBeNull();
  });

  it('summarises the COP curve and the learned model', () => {
    expect(thermalView(null)).toEqual({ cop: null, nrmse: null });
    const v = thermalView({
      grid_preheat: false,
      cop: {
        assumed: true,
        points: [
          { outdoor_c: -7, cop: 2.4 },
          { outdoor_c: 7, cop: 3.9 },
        ],
      },
      learned: { nrmse: 0.12 },
    } as never);
    expect(v).toEqual({ cop: { min: 2.4, max: 3.9, assumed: true }, nrmse: 0.12 });
  });

  it('knows which jobs can still be cancelled', () => {
    const job = (state: string) => ({ state }) as never;
    expect(jobOpen(null)).toBe(false);
    expect(jobOpen(job('armed'))).toBe(true);
    expect(jobOpen(job('running'))).toBe(true);
    expect(jobOpen(job('done'))).toBe(false);
    expect(jobOpen(job('cancelled'))).toBe(false);
  });
});
