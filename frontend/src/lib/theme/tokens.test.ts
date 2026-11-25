// SPDX-License-Identifier: Apache-2.0
// @vitest-environment node
// Contrast of our default tokens (phase-7 criterion 2, ADR-0019 guard): text >= 4.5:1, UI and
// chart lines >= 3:1 against their surfaces, light and dark, with the color-mix() tints and with
// the static fallbacks of older webviews. tokens.css is parsed, not copied.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { luminance } from './bridge';
import { CHART_TOKEN_DEFAULTS } from './tokens';

const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf-8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
);

/** Declarations of the first rule whose selector is exactly `selector`. */
function block(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`no rule ${selector}`);
  const open = css.indexOf('{', start);
  const close = css.indexOf('}', open);
  const out: Record<string, string> = {};
  for (const decl of css.slice(open + 1, close).split(';')) {
    const i = decl.indexOf(':');
    if (i < 0) continue;
    const name = decl.slice(0, i).trim();
    if (name.startsWith('--')) out[name] = decl.slice(i + 1).trim();
  }
  return out;
}

const LIGHT = block(':root');
const DARK = block(":root[data-theme='dark']");
const DARK_MEDIA = block(":root:not([data-theme='light'])");
const MIXES = block(':root:root:root');

type Rgb = [number, number, number];

function hexRgb(hex: string): Rgb {
  const h = hex.slice(1);
  const full = h.length === 3 ? [...h].map((c) => c + c).join('') : h;
  return [0, 2, 4].map((i) => Number.parseInt(full.slice(i, i + 2), 16)) as Rgb;
}
const rgbHex = (c: Rgb): string =>
  `#${c.map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')}`;

/** Resolves a token to `#rrggbb`: var() references and color-mix(in srgb, A p%, B). */
function resolve(vars: Record<string, string>, value: string, depth = 0): string {
  if (depth > 20) throw new Error(`cycle at ${value}`);
  const v = value.trim();
  const ref = /^var\((--[\w-]+)\)$/.exec(v);
  if (ref) {
    const next = vars[ref[1]!];
    if (next === undefined) throw new Error(`unknown token ${ref[1]}`);
    return resolve(vars, next, depth + 1);
  }
  const mix = /^color-mix\(in srgb,\s*(.+?)\s+([\d.]+)%,\s*(.+)\)$/.exec(v);
  if (mix) {
    const a = hexRgb(resolve(vars, mix[1]!, depth + 1));
    const b = hexRgb(resolve(vars, mix[3]!, depth + 1));
    const p = Number(mix[2]) / 100;
    return rgbHex(a.map((x, i) => x * p + b[i]! * (1 - p)) as Rgb);
  }
  if (/^#[0-9a-f]{3,6}$/i.test(v)) return v.toLowerCase();
  throw new Error(`cannot resolve ${v}`);
}

function ratio(a: string, b: string): number {
  const la = luminance(a)!;
  const lb = luminance(b)!;
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const SCHEMES = {
  'light, color-mix': { ...LIGHT, ...MIXES },
  'light, static fallback': LIGHT,
  'dark, color-mix': { ...LIGHT, ...DARK, ...MIXES },
  'dark, static fallback': { ...LIGHT, ...DARK },
};

const SURFACES = ['--sc-surface', '--sc-surface-raised', '--sc-surface-sunken', '--sc-bg'];
const STATUS = ['accent', 'ok', 'warn', 'error', 'info'];
const ENERGY = ['pv', 'grid-in', 'grid-out', 'bat-in', 'bat-out', 'house', 'device'];

/** [foreground, background, minimum ratio] */
function pairs(): [string, string, number][] {
  const out: [string, string, number][] = [];
  for (const bg of SURFACES) {
    out.push(['--sc-text', bg, 4.5], ['--sc-text-muted', bg, 4.5]);
    out.push(['--sc-accent', bg, 4.5], ['--sc-error', bg, 4.5]);
  }
  for (const s of STATUS) {
    out.push([`--sc-${s}`, '--sc-surface', 4.5]); // status text on a card
    out.push([`--sc-${s}`, `--sc-${s}-bg`, 4.5]); // chip text on its tint
    out.push(['--sc-text', `--sc-${s}-bg`, 4.5]); // banner text on the tint
    out.push(['--sc-text-muted', `--sc-${s}-bg`, 4.5]);
    out.push([`--sc-${s}-fg`, `--sc-${s}`, 4.5]); // text on the solid colour
  }
  out.push(['--sc-text-on-accent', '--sc-accent', 4.5]);
  out.push(['--sc-outline-strong', '--sc-surface', 3]); // input borders
  for (const e of ENERGY) {
    out.push(
      [`--sc-energy-${e}`, '--sc-surface', 3],
      [`--sc-energy-${e}`, '--sc-surface-raised', 3],
    );
  }
  return out;
}

describe('design tokens', () => {
  for (const [scheme, vars] of Object.entries(SCHEMES)) {
    it(`meet the contrast pairs (${scheme})`, () => {
      const failures = pairs()
        .map(([fg, bg, min]) => {
          const r = ratio(resolve(vars, `var(${fg})`), resolve(vars, `var(${bg})`));
          return r < min ? `${fg} on ${bg}: ${r.toFixed(2)} < ${min}` : null;
        })
        .filter(Boolean);
      expect(failures).toEqual([]);
    });
  }

  it('declare dark mode identically for prefers-color-scheme and data-theme', () => {
    expect(DARK_MEDIA).toEqual(DARK);
  });

  it('keep the static tints within a step of the color-mix() result', () => {
    for (const vars of [LIGHT, { ...LIGHT, ...DARK }]) {
      for (const name of Object.keys(MIXES)) {
        const mixed = hexRgb(resolve({ ...vars, ...MIXES }, `var(${name})`));
        const fixed = hexRgb(resolve(vars, `var(${name})`));
        const delta = Math.max(...mixed.map((v, i) => Math.abs(v - fixed[i]!)));
        expect(delta, name).toBeLessThanOrEqual(2);
      }
    }
  });

  it('mirror the light defaults in tokens.ts', () => {
    for (const [name, value] of Object.entries(CHART_TOKEN_DEFAULTS)) {
      if (name === '--sc-font-family') continue;
      expect(resolve(LIGHT, `var(${name})`), name).toBe(value);
    }
  });

  it('cover spacing, radius, type and motion', () => {
    expect(LIGHT['--sc-space-1']).toBe('0.25rem');
    expect(LIGHT['--sc-space-8']).toBe('4rem');
    expect(LIGHT['--sc-radius-lg']).toBe('16px');
    expect(LIGHT['--sc-font-2xl']).toBe('1.75rem');
    expect(LIGHT['--sc-motion-fast']).toBe('120ms');
    // Aliases stay until the last view moved.
    expect(LIGHT['--sc-radius']).toBe('var(--sc-radius-md)');
    expect(LIGHT['--sc-gap']).toBe('var(--sc-space-4)');
  });
});
