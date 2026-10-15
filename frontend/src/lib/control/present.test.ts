// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { describeAction } from './actions';
import { device } from './fixtures';
import { clock, holdDay, openProposals, secondsLeft, stateView } from './present';

describe('holdDay', () => {
  const now = new Date(2026, 5, 21, 23, 0);
  it('names today, tomorrow and later by calendar day', () => {
    expect(holdDay(new Date(2026, 5, 21, 23, 30), now)).toBe('today');
    expect(holdDay(new Date(2026, 5, 22, 4, 0), now)).toBe('tomorrow');
    expect(holdDay(new Date(2026, 5, 24, 4, 0), now)).toBe('later');
  });
});

describe('secondsLeft', () => {
  const now = Date.parse('2026-06-21T09:30:00Z');
  it('counts down and never goes below zero', () => {
    expect(secondsLeft('2026-06-21T09:32:00Z', now)).toBe(120);
    expect(secondsLeft('2026-06-21T09:30:00.400Z', now)).toBe(1);
    expect(secondsLeft('2026-06-21T09:29:00Z', now)).toBe(0);
  });
  it('is null without an expiry', () => {
    expect(secondsLeft(null, now)).toBeNull();
    expect(secondsLeft('garbage', now)).toBeNull();
  });
  it('formats m:ss', () => {
    expect(clock(125)).toBe('2:05');
    expect(clock(9)).toBe('0:09');
  });
});

describe('stateView', () => {
  it('shows the set-point against the base for a running climate', () => {
    expect(stateView(device())).toEqual({ kind: 'setpoint', setpoint: 24, base: 25 });
  });
  it('shows on/off, a step, a level or unknown', () => {
    expect(stateView(device({ state: { on: false, quality: 'ok' } }))).toEqual({ kind: 'off' });
    expect(stateView(device({ kind: 'switch', state: { on: true, quality: 'ok' } }))).toEqual({
      kind: 'on',
    });
    expect(
      stateView(device({ kind: 'switch', state: { on: true, step: 2, quality: 'ok' } })),
    ).toEqual({ kind: 'step', step: 2 });
    expect(
      stateView(device({ kind: 'number_load', state: { on: false, value: 1200, quality: 'ok' } })),
    ).toEqual({ kind: 'level', value: 1200 });
    expect(stateView(device({ state: null }))).toEqual({ kind: 'unknown' });
    expect(stateView(device({ state: { on: null, quality: 'unavailable' } }))).toEqual({
      kind: 'unknown',
    });
  });
});

describe('proposals', () => {
  it('drops expired proposals', () => {
    const now = Date.parse('2026-06-21T09:30:00Z');
    const mk = (id: string, expires_at: string | null) =>
      ({ intent: { id }, expires_at }) as Parameters<typeof openProposals>[0][number];
    const open = openProposals(
      [mk('a', '2026-06-21T09:29:00Z'), mk('b', '2026-06-21T09:31:00Z'), mk('c', null)],
      now,
    );
    expect(open.map((p) => p.intent.id)).toEqual(['b', 'c']);
  });
});

describe('describeAction', () => {
  const w = (n: number) => `${n} W`;
  it('maps every action kind to a catalogue key', () => {
    expect(describeAction({ kind: 'switch', on: true }, w).key).toBe('action.switch_on');
    expect(describeAction({ kind: 'switch', on: false }, w).key).toBe('action.switch_off');
    expect(describeAction({ kind: 'power_setpoint', power_w: 900 }, w)).toEqual({
      key: 'action.power_setpoint',
      args: { power: '900 W' },
    });
    expect(describeAction({ kind: 'setpoint', setpoint_c: 23, base_c: 25 }, w).args).toEqual({
      value: 23,
      base: 25,
    });
    expect(describeAction({ kind: 'step', step: 2 }, w).key).toBe('action.step');
    expect(describeAction({ kind: 'mode', mode: 'eco' }, w).key).toBe('action.mode');
    expect(describeAction({ kind: 'release' }, w).key).toBe('action.release');
  });
});
