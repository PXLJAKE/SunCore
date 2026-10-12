// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { FlowValue } from './api/types';
import {
  activityOf,
  cycleSeconds,
  directionOf,
  edgeAngle,
  isUsable,
  pvDirection,
  speedLevel,
  worstQuality,
} from './flow';

const ok = (value: number | null): FlowValue => ({ value, quality: 'ok' });

describe('directionOf', () => {
  it('maps the sign to the edge direction', () => {
    expect(directionOf(ok(1500))).toBe('forward');
    expect(directionOf(ok(-1500))).toBe('reverse');
  });
  it('treats noise below the idle threshold as idle', () => {
    expect(directionOf(ok(0))).toBe('idle');
    expect(directionOf(ok(9.9))).toBe('idle');
    expect(directionOf(ok(-9.9))).toBe('idle');
    expect(directionOf(ok(10))).toBe('forward');
  });
  it('is idle without a usable value', () => {
    expect(directionOf(null)).toBe('idle');
    expect(directionOf({ value: null, quality: 'unavailable' })).toBe('idle');
    expect(directionOf({ value: 500, quality: 'invalid' })).toBe('idle');
    expect(directionOf({ value: Number.NaN, quality: 'ok' })).toBe('idle');
  });
  it('keeps the direction of stale values', () => {
    expect(directionOf({ value: -500, quality: 'stale' })).toBe('reverse');
  });
});

describe('pvDirection', () => {
  it('never flows backwards', () => {
    expect(pvDirection(ok(2000))).toBe('forward');
    expect(pvDirection(ok(-50))).toBe('idle');
  });
});

describe('quality handling', () => {
  it('shows stale values but does not animate them', () => {
    const stale: FlowValue = { value: 800, quality: 'stale' };
    expect(isUsable(stale)).toBe(true);
    expect(speedLevel(stale)).toBe(0);
  });
  it('hides unavailable and invalid values', () => {
    expect(isUsable({ value: 800, quality: 'unavailable' })).toBe(false);
    expect(isUsable({ value: 800, quality: 'invalid' })).toBe(false);
    expect(isUsable({ value: null, quality: 'ok' })).toBe(false);
  });
  it('ranks the worst quality', () => {
    expect(worstQuality('ok', 'stale')).toBe('stale');
    expect(worstQuality('stale', 'invalid', 'ok')).toBe('invalid');
    expect(worstQuality('invalid', 'unavailable')).toBe('unavailable');
    expect(worstQuality()).toBe('ok');
  });
});

describe('speedLevel', () => {
  it('is monotonic in |power| and symmetric in sign', () => {
    const powers = [5, 50, 200, 800, 3000, 9000];
    const levels = powers.map((w) => speedLevel(ok(w)));
    expect(levels).toEqual([0, 1, 2, 3, 4, 5]);
    expect(speedLevel(ok(-3000))).toBe(speedLevel(ok(3000)));
  });
  it('maps levels to faster cycles', () => {
    const cycles = [1, 2, 3, 4, 5].map(cycleSeconds);
    expect([...cycles].sort((a, b) => b - a)).toEqual(cycles);
    expect(new Set(cycles).size).toBe(5);
  });
});

describe('activityOf', () => {
  it('names what each node is doing', () => {
    expect(activityOf('grid', ok(500))).toBe('importing');
    expect(activityOf('grid', ok(-500))).toBe('exporting');
    expect(activityOf('battery', ok(500))).toBe('charging');
    expect(activityOf('battery', ok(-500))).toBe('discharging');
    expect(activityOf('pv', ok(500))).toBe('producing');
    expect(activityOf('house', ok(500))).toBe('consuming');
    expect(activityOf('house', ok(0))).toBe('idle');
  });
});

describe('edgeAngle', () => {
  it('points along the edge and flips for reverse', () => {
    expect(edgeAngle(0, 0, 10, 0, 'forward')).toBe(0);
    expect(edgeAngle(0, 0, 10, 0, 'reverse')).toBe(180);
    expect(edgeAngle(0, 0, 0, 10, 'forward')).toBe(90);
  });
});
