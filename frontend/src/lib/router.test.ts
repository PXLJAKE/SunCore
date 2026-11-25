// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { destinationOf, parseLocation, settingsHash, stepHash } from './router.svelte';

describe('destinationOf', () => {
  it('groups the routes into the four destinations', () => {
    expect(destinationOf('now')).toBe('now');
    expect(destinationOf('plan')).toBe('plan');
    expect(destinationOf('history')).toBe('analysis');
    expect(destinationOf('decisions')).toBe('analysis');
    expect(destinationOf('whatif')).toBe('analysis');
    expect(destinationOf('settings')).toBe('settings');
    expect(destinationOf('setup')).toBe('settings');
  });
});

describe('parseLocation', () => {
  it('maps hashes to routes and defaults to now', () => {
    expect(parseLocation('')).toEqual({ route: 'now', step: null });
    expect(parseLocation('#/')).toEqual({ route: 'now', step: null });
    expect(parseLocation('#/settings')).toEqual({ route: 'settings', step: null, page: null });
    expect(parseLocation('#/plan')).toEqual({ route: 'plan', step: null });
    expect(parseLocation('#/plan?x=1')).toEqual({ route: 'plan', step: null });
    expect(parseLocation('#/history')).toEqual({ route: 'history', step: null });
    expect(parseLocation('#/history?period=day')).toEqual({ route: 'history', step: null });
    expect(parseLocation('#/whatif')).toEqual({ route: 'whatif', step: null });
    expect(parseLocation('#/decisions')).toEqual({ route: 'decisions', step: null });
    expect(parseLocation('#/settings?x=1')).toEqual({ route: 'settings', step: null, page: null });
    expect(parseLocation('#/unknown')).toEqual({ route: 'now', step: null });
  });
  it('parses wizard steps and falls back to resuming for unknown ones', () => {
    expect(parseLocation('#/setup')).toEqual({ route: 'setup', step: null });
    expect(parseLocation('#/setup/')).toEqual({ route: 'setup', step: null });
    expect(parseLocation('#/setup/grid')).toEqual({ route: 'setup', step: 'grid' });
    expect(parseLocation('#/setup/grid/?a=1')).toEqual({ route: 'setup', step: 'grid' });
    expect(parseLocation('#/setup/nonsense')).toEqual({ route: 'setup', step: null });
  });
  it('parses settings sub-pages; an unknown one is the index', () => {
    expect(parseLocation('#/settings/ha')).toEqual({ route: 'settings', step: null, page: 'ha' });
    expect(parseLocation(settingsHash('advanced')).page).toBe('advanced');
    expect(parseLocation('#/settings/nope')).toEqual({ route: 'settings', step: null, page: null });
    expect(destinationOf(parseLocation('#/settings/system').route)).toBe('settings');
  });
  it('builds step hashes', () => {
    expect(stepHash('devices')).toBe('#/setup/devices');
    expect(parseLocation(stepHash('summary')).step).toBe('summary');
  });
});
