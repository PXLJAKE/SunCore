// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from 'vitest';
import {
  applyTheme,
  clearTheme,
  luminance,
  readParentTheme,
  schemeOf,
  startThemeBridge,
  THEME_MAP,
} from './bridge';

/** A window whose parent is another jsdom-like document with the given root style. */
function fakeWindow(parentVars: Record<string, string> | 'throws' | 'self'): Window {
  const win = {} as Window;
  if (parentVars === 'self') {
    (win as { parent: Window }).parent = win;
    return win;
  }
  const parent = {
    get document(): Document {
      if (parentVars === 'throws') throw new DOMException('Blocked a frame', 'SecurityError');
      return { documentElement: {} } as Document;
    },
    getComputedStyle: () => ({
      getPropertyValue: (name: string) => (parentVars === 'throws' ? '' : (parentVars[name] ?? '')),
    }),
  };
  (win as { parent: unknown }).parent = parent;
  return win;
}

describe('readParentTheme', () => {
  it('copies the mapped HA variables', () => {
    const vars = readParentTheme(
      fakeWindow({
        '--primary-color': ' #03a9f4 ',
        '--card-background-color': '#111111',
        '--x': '1',
      }),
    );
    expect(vars).toEqual({ '--primary-color': '#03a9f4', '--card-background-color': '#111111' });
  });
  it('returns null without a parent, with a cross-origin parent or without variables', () => {
    expect(readParentTheme(fakeWindow('self'))).toBeNull();
    expect(readParentTheme(fakeWindow('throws'))).toBeNull();
    expect(readParentTheme(fakeWindow({}))).toBeNull();
  });
  it('maps only variables listed in THEME_MAP', () => {
    expect(THEME_MAP.map(([ha]) => ha)).toContain('--primary-text-color');
  });
});

describe('schemeOf / luminance', () => {
  it('detects dark and light backgrounds', () => {
    expect(luminance('#000')).toBeCloseTo(0);
    expect(luminance('#ffffff')).toBeCloseTo(1);
    expect(luminance('rgb(28, 28, 30)')).toBeLessThan(0.05);
    expect(luminance('hsl(0 0% 0%)')).toBeNull();
    expect(schemeOf({ '--primary-background-color': '#111111' })).toBe('dark');
    expect(schemeOf({ '--card-background-color': 'rgb(255,255,255)' })).toBe('light');
    expect(schemeOf({})).toBeNull();
  });
});

describe('applyTheme / startThemeBridge', () => {
  it('sets and clears the variables on the root', () => {
    const root = document.createElement('html');
    applyTheme(root, { '--primary-color': '#ff0000', '--primary-background-color': '#000000' });
    expect(root.style.getPropertyValue('--primary-color')).toBe('#ff0000');
    expect(root.style.colorScheme).toBe('dark');
    clearTheme(root);
    expect(root.style.getPropertyValue('--primary-color')).toBe('');
    expect(root.style.colorScheme).toBe('');
  });
  it('applies the parent theme once and survives a blocked parent', () => {
    const root = document.createElement('html');
    const win = fakeWindow({ '--primary-color': '#abcdef' });
    const stop = startThemeBridge(win, root);
    expect(root.style.getPropertyValue('--primary-color')).toBe('#abcdef');
    stop();
    const blocked = document.createElement('html');
    expect(() => startThemeBridge(fakeWindow('throws'), blocked)()).not.toThrow();
    expect(blocked.style.getPropertyValue('--primary-color')).toBe('');
  });
  it('leaves a forced scheme alone', () => {
    const root = document.createElement('html');
    root.dataset.theme = 'dark';
    startThemeBridge(fakeWindow({ '--primary-color': '#abcdef' }), root)();
    expect(root.style.getPropertyValue('--primary-color')).toBe('');
  });
});
