// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import { createTranslator, resolveLocale } from './core';
import { formatPower } from './format';
import de from './locales/de.json';
import en from './locales/en.json';

const tr = createTranslator({ en, de });

describe('resolveLocale', () => {
  it('maps German variants to de and everything else to en', () => {
    expect(resolveLocale('de')).toBe('de');
    expect(resolveLocale('de-AT')).toBe('de');
    expect(resolveLocale('DE_ch')).toBe('de');
    expect(resolveLocale('fr-FR')).toBe('en');
    expect(resolveLocale('en-GB')).toBe('en');
    expect(resolveLocale('')).toBe('en');
    expect(resolveLocale(undefined)).toBe('en');
  });
  it('takes the first supported language of a preference list', () => {
    expect(resolveLocale(['fr-FR', 'de-DE', 'en'])).toBe('de');
    expect(resolveLocale(['fr', 'es'])).toBe('en');
  });
});

describe('catalogues', () => {
  it('have identical key sets', () => {
    expect(Object.keys(de).sort()).toEqual(Object.keys(en).sort());
  });
  it('contain the backend reason and error keys', () => {
    for (const key of [
      'safety.shadow_mode',
      'safety.emergency_stop',
      'api.error.ha_unavailable',
      'api.error.import_too_large',
      'api.error.invalid_json',
      'api.error.import_rejected',
      'api.error.not_found',
      'api.error.forbidden',
    ]) {
      expect(tr.has(key, 'en'), key).toBe(true);
      expect(tr.has(key, 'de'), key).toBe(true);
    }
  });
});

describe('sign check messages', () => {
  it('end sentences cleanly for every direction (no stray space before the full stop)', () => {
    for (const locale of ['en', 'de'] as const) {
      for (const dir of ['importing', 'exporting', 'idle']) {
        const text = tr.render(locale, 'signcheck.grid', { dir, power: '1.2 kW' });
        expect(text, `${locale} ${dir}`).not.toMatch(/\s[.,?]|\s{2}/);
        expect(text).toContain('1.2 kW');
      }
    }
    expect(tr.render('de', 'signcheck.grid', { dir: 'importing', power: '1,2 kW' })).toBe(
      'Am Netzanschluss werden gerade 1,2 kW bezogen. Stimmt das?',
    );
    expect(tr.render('de', 'signcheck.grid', { dir: 'exporting', power: '1,2 kW' })).toBe(
      'Am Netzanschluss werden gerade 1,2 kW eingespeist. Stimmt das?',
    );
    expect(tr.render('de', 'signcheck.grid', { dir: 'idle', power: '3 W' })).toBe(
      'Am Netzanschluss fließen gerade 3 W. Stimmt das?',
    );
  });
});

describe('translator', () => {
  it('renders ICU plurals per locale', () => {
    expect(tr.render('en', 'settings.sum.devices', { count: 0 })).toBe('No devices');
    expect(tr.render('en', 'settings.sum.devices', { count: 1 })).toBe('1 device');
    expect(tr.render('en', 'settings.sum.devices', { count: 3 })).toBe('3 devices');
    expect(tr.render('de', 'settings.sum.devices', { count: 1 })).toBe('1 Gerät');
    expect(tr.render('de', 'settings.sum.devices', { count: 2 })).toBe('2 Geräte');
  });
  it('formats numbers in the message locale', () => {
    const cat = { 'x.n': 'Value {n, number}' };
    const t2 = createTranslator({ en: cat, de: cat });
    expect(t2.render('en', 'x.n', { n: 1234.5 })).toBe('Value 1,234.5');
    expect(t2.render('de', 'x.n', { n: 1234.5 })).toBe('Value 1.234,5');
  });
  it('falls back to English for keys missing in German, then to the key', () => {
    const t2 = createTranslator({ en: { a: 'English' }, de: {} });
    expect(t2.render('de', 'a')).toBe('English');
    expect(t2.render('de', 'missing.key')).toBe('missing.key');
  });
  it('does not resolve inherited object properties as keys', () => {
    for (const key of ['constructor', 'toString', '__proto__', 'hasOwnProperty']) {
      expect(tr.has(key, 'en'), key).toBe(false);
      expect(tr.render('en', key)).toBe(key);
    }
  });
  it('does not blank the UI when an argument is missing', () => {
    expect(tr.render('en', 'unit.w')).not.toBe('');
  });
});

describe('formatPower', () => {
  it('uses whole watts below 1000 W', () => {
    expect(formatPower(0, 'en')).toEqual({ unit: 'w', text: '0' });
    expect(formatPower(849.6, 'en')).toEqual({ unit: 'w', text: '850' });
    expect(formatPower(-120, 'en')).toEqual({ unit: 'w', text: '-120' });
  });
  it('switches to kW from 1000 W with locale decimals', () => {
    expect(formatPower(1000, 'en')).toEqual({ unit: 'kw', text: '1' });
    expect(formatPower(1234, 'en')).toEqual({ unit: 'kw', text: '1.23' });
    expect(formatPower(1234, 'de')).toEqual({ unit: 'kw', text: '1,23' });
    expect(formatPower(12500, 'de')).toEqual({ unit: 'kw', text: '12,5' });
  });
  it('does not show 1000 W when 999.6 rounds up', () => {
    expect(formatPower(999.6, 'en').unit).toBe('kw');
  });
});

describe('time zone', () => {
  it('shows clock times in the given zone, not the browser zone', async () => {
    const { clockParts, formatTime, validZone } = await import('./format');
    const at = new Date('2026-06-21T22:30:00Z');
    expect(clockParts(at, 'Europe/Berlin')).toEqual({ hour: 0, minute: 30 });
    expect(clockParts(at, 'UTC')).toEqual({ hour: 22, minute: 30 });
    expect(formatTime(at, 'en', 'America/New_York').replace(/\s/g, ' ')).toBe('06:30 PM');
    expect(validZone('Europe/Berlin')).toBe('Europe/Berlin');
    expect(validZone('Not/AZone')).toBeUndefined();
    expect(validZone(null)).toBeUndefined();
  });
});
