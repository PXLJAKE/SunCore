// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { boostCommand, localDateTimeValue, pauseCommand } from './overrideForm';

describe('boostCommand', () => {
  it('accepts whole minutes from 1 to 1440', () => {
    expect(boostCommand(30)).toEqual({ ok: true, command: { action: 'boost', minutes: 30 } });
    expect(boostCommand(1).ok).toBe(true);
    expect(boostCommand(1440).ok).toBe(true);
  });

  it('rejects empty, zero, fractions and too long durations', () => {
    for (const bad of [null, 0, -5, 1.5, 1441, Number.NaN]) {
      expect(boostCommand(bad), String(bad)).toEqual({ ok: false, error: 'boost_minutes' });
    }
  });
});

describe('pauseCommand', () => {
  const now = new Date(2026, 5, 21, 9, 30);

  it('pauses until resumed when no time is given', () => {
    expect(pauseCommand('', now)).toEqual({ ok: true, command: { action: 'pause' } });
    expect(pauseCommand('   ', now)).toEqual({ ok: true, command: { action: 'pause' } });
  });

  it('sends the local time as an absolute instant', () => {
    const r = pauseCommand('2026-06-21T18:00', now);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.command.until).toBe(new Date(2026, 5, 21, 18, 0).toISOString());
  });

  it('rejects a time in the past or right now', () => {
    expect(pauseCommand('2026-06-21T09:30', now)).toEqual({ ok: false, error: 'pause_past' });
    expect(pauseCommand('2026-06-20T18:00', now)).toEqual({ ok: false, error: 'pause_past' });
  });

  it('rejects text that is no date and time', () => {
    expect(pauseCommand('tomorrow', now)).toEqual({ ok: false, error: 'pause_invalid' });
    expect(pauseCommand('2026-13-45T99:99', now)).toEqual({ ok: false, error: 'pause_invalid' });
  });
});

describe('localDateTimeValue', () => {
  it('formats the local minute for a datetime-local input', () => {
    expect(localDateTimeValue(new Date(2026, 0, 5, 7, 4))).toBe('2026-01-05T07:04');
  });
});
