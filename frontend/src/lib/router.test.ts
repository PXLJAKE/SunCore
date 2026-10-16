// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { parseLocation, stepHash } from './router.svelte';

describe('parseLocation', () => {
  it('maps hashes to routes and defaults to now', () => {
    expect(parseLocation('')).toEqual({ route: 'now', step: null });
    expect(parseLocation('#/')).toEqual({ route: 'now', step: null });
    expect(parseLocation('#/settings')).toEqual({ route: 'settings', step: null });
    expect(parseLocation('#/decisions')).toEqual({ route: 'decisions', step: null });
    expect(parseLocation('#/settings?x=1')).toEqual({ route: 'settings', step: null });
    expect(parseLocation('#/unknown')).toEqual({ route: 'now', step: null });
  });
  it('parses wizard steps and falls back to resuming for unknown ones', () => {
    expect(parseLocation('#/setup')).toEqual({ route: 'setup', step: null });
    expect(parseLocation('#/setup/')).toEqual({ route: 'setup', step: null });
    expect(parseLocation('#/setup/grid')).toEqual({ route: 'setup', step: 'grid' });
    expect(parseLocation('#/setup/grid/?a=1')).toEqual({ route: 'setup', step: 'grid' });
    expect(parseLocation('#/setup/nonsense')).toEqual({ route: 'setup', step: null });
  });
  it('builds step hashes', () => {
    expect(stepHash('devices')).toBe('#/setup/devices');
    expect(parseLocation(stepHash('summary')).step).toBe('summary');
  });
});
