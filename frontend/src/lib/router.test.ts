// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { parseRoute } from './router.svelte';

describe('parseRoute', () => {
  it('maps hashes to routes and defaults to now', () => {
    expect(parseRoute('')).toBe('now');
    expect(parseRoute('#/')).toBe('now');
    expect(parseRoute('#/settings')).toBe('settings');
    expect(parseRoute('#/settings?x=1')).toBe('settings');
    expect(parseRoute('#/unknown')).toBe('now');
  });
});
