// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { createTranslator } from './core';
import de from './locales/de.json';
import en from './locales/en.json';

describe('lazy catalogues', () => {
  it('falls back to English until a catalogue is set, then uses it', () => {
    const lazy = createTranslator({ en });
    expect(lazy.loaded('de')).toBe(false);
    expect(lazy.has('log.title', 'de')).toBe(false);
    expect(lazy.render('de', 'log.title')).toBe(en['log.title']);
    lazy.set('de', de);
    expect(lazy.loaded('de')).toBe(true);
    expect(lazy.render('de', 'log.title')).toBe(de['log.title']);
    lazy.set('de', { 'log.title': 'X' });
    expect(lazy.render('de', 'log.title')).toBe('X');
    expect(lazy.render('de', 'log.why')).toBe(en['log.why']);
  });
});
