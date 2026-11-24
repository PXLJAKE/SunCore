// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import type { Param } from '../api/types';
import {
  groupParams,
  isEdited,
  matchesQuery,
  needsRestart,
  parseRaw,
  planChanges,
  safetyChanges,
} from './model';
import { param } from './fixtures';

const P = (over: Partial<Param>): Param => param(over);

describe('parseRaw', () => {
  it('parses numbers and enforces the type and the hard range', () => {
    const f = P({ type: 'float', minimum: 1, maximum: 10, value: 5 });
    expect(parseRaw(f, '2.5')).toEqual({ ok: true, value: 2.5 });
    expect(parseRaw(f, '11')).toMatchObject({
      ok: false,
      key: 'api.validation.param_out_of_range',
    });
    expect(parseRaw(f, '0.5')).toMatchObject({
      ok: false,
      key: 'api.validation.param_out_of_range',
    });
    expect(parseRaw(f, '')).toMatchObject({ ok: false, key: 'api.validation.param_wrong_type' });
    const i = P({ type: 'int', minimum: 1, maximum: 10, value: 5 });
    expect(parseRaw(i, '2.5')).toMatchObject({ ok: false, key: 'api.validation.param_wrong_type' });
    expect(parseRaw(i, '3')).toEqual({ ok: true, value: 3 });
  });
  it('handles bool, enum and entity', () => {
    expect(parseRaw(P({ type: 'bool', value: false }), true)).toEqual({ ok: true, value: true });
    const e = P({ type: 'enum', choices: ['off', 'shadow'], value: 'off' });
    expect(parseRaw(e, 'shadow')).toEqual({ ok: true, value: 'shadow' });
    expect(parseRaw(e, 'auto')).toMatchObject({ ok: false });
    const ent = P({ type: 'entity', value: null });
    expect(parseRaw(ent, ' ')).toEqual({ ok: true, value: null });
    expect(parseRaw(ent, 'sensor.x')).toEqual({ ok: true, value: 'sensor.x' });
  });
});

describe('drafts', () => {
  const a = P({ key: 'a', value: 5, minimum: 0, maximum: 10 });
  const b = P({ key: 'b', value: 3, safety: true, minimum: 0, maximum: 10 });
  it('an unchanged or untouched input is not an edit', () => {
    expect(isEdited(a, {})).toBe(false);
    expect(isEdited(a, { a: '5' })).toBe(false);
    expect(isEdited(a, { a: '6' })).toBe(true);
  });
  it('plans valid changes and reports invalid ones separately', () => {
    const plan = planChanges([a, b], { a: '6', b: '99' });
    expect(plan.changes).toEqual({ a: 6 });
    expect(plan.errors.b?.key).toBe('api.validation.param_out_of_range');
  });
  it('lists the safety-tagged changes with old and new value', () => {
    expect(safetyChanges([a, b], { a: 6, b: 4 })).toEqual([{ key: 'b', from: 3, to: 4 }]);
    expect(safetyChanges([a, b], { a: 6 })).toEqual([]);
  });
  it('knows when a restart is needed', () => {
    const r = P({ key: 'r', restart: true });
    expect(needsRestart([a, r], ['a'])).toBe(false);
    expect(needsRestart([a, r], ['a', 'r'])).toBe(true);
  });
});

describe('grouping and search', () => {
  const ps = [
    P({ key: 'x.one', group: 'g1', modified: true }),
    P({ key: 'x.two', group: 'g1' }),
    P({ key: 'y.one', group: 'g2' }),
  ];
  it('keeps the server order, counts modified and drops empty groups', () => {
    const all = groupParams(ps, ['g1', 'g2'], () => true);
    expect(all.map((g) => [g.name, g.params.length, g.modified])).toEqual([
      ['g1', 2, 1],
      ['g2', 1, 0],
    ]);
    expect(groupParams(ps, ['g1', 'g2'], (p) => p.key.startsWith('y')).map((g) => g.name)).toEqual([
      'g2',
    ]);
  });
  it('matches case-insensitively over all haystacks', () => {
    expect(matchesQuery('', 'a')).toBe(true);
    expect(matchesQuery('TICK', 'controller.tick_s', 'Interval')).toBe(true);
    expect(matchesQuery('zzz', 'a', 'b')).toBe(false);
  });
});
