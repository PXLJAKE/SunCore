// SPDX-License-Identifier: Apache-2.0
// Golden file for the backend ICU renderer (ADR-0016 decision 6): every key of the generated
// backend catalogue, rendered with `intl-messageformat` for sample arguments. The Python test
// `tests/i18n/test_golden.py` renders the same cases with `suncore.i18n` and compares.
// Regenerate with `UPDATE_GOLDEN=1 npm test -- golden` after changing a catalogue.
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { IntlMessageFormat } from 'intl-messageformat';
import { describe, expect, it } from 'vitest';

type Args = Record<string, string | number>;
interface Case {
  lang: string;
  key: string;
  args: Args;
  expected: string;
}

// ICU element types of @formatjs/icu-messageformat-parser (TYPE enum).
const LITERAL = 0;
const ARGUMENT = 1;
const NUMBER = 2;
const SELECT = 5;
const PLURAL = 6;

interface Element {
  type: number;
  value?: string;
  options?: Record<string, { value: Element[] }>;
}

const ROOT = resolve(__dirname, '../../../..');
const CATALOGUE = resolve(ROOT, 'suncore/i18n/catalogue');
const GOLDEN = resolve(ROOT, 'tests/fixtures/i18n_golden.json');
const LANGS = ['en', 'de'] as const;

const NUMBERS = [0, 1, 2, 5, 21, 1234.5, -3.25, 0.125];
const PLURALS = [0, 1, 2, 5, 21];

function collect(elements: Element[], into: Map<string, Set<string | number>>): void {
  for (const el of elements) {
    if (el.type === LITERAL) continue;
    const name = (el as unknown as { value: string }).value;
    if (el.type === ARGUMENT) {
      into.set(name, into.get(name) ?? new Set(['abc', 42]));
    } else if (el.type === NUMBER) {
      into.set(name, new Set(NUMBERS));
    } else if (el.type === PLURAL || el.type === SELECT) {
      const set = new Set<string | number>(el.type === PLURAL ? PLURALS : ['zzz']);
      for (const key of Object.keys(el.options ?? {})) {
        if (el.type === SELECT) set.add(key);
        else if (key.startsWith('=')) set.add(Number(key.slice(1)));
      }
      into.set(name, set);
    }
    for (const branch of Object.values(el.options ?? {})) collect(branch.value, into);
  }
}

function build(): Case[] {
  const cases: Case[] = [];
  for (const lang of LANGS) {
    const messages = JSON.parse(readFileSync(resolve(CATALOGUE, `${lang}.json`), 'utf8')) as Record<
      string,
      string
    >;
    for (const [key, source] of Object.entries(messages)) {
      const fmt = new IntlMessageFormat(source, lang);
      const wanted = new Map<string, Set<string | number>>();
      collect(fmt.getAst() as unknown as Element[], wanted);
      const values = [...wanted.entries()].map(([name, set]) => [name, [...set]] as const);
      const rounds = Math.min(8, Math.max(1, ...values.map(([, v]) => v.length)));
      for (let i = 0; i < rounds; i += 1) {
        const args: Args = {};
        for (const [name, list] of values) args[name] = list[i % list.length] as string | number;
        cases.push({ lang, key, args, expected: String(fmt.format(args as Record<string, never>)) });
      }
    }
  }
  return cases;
}

describe('backend catalogue golden file', () => {
  it('matches intl-messageformat for every generated key', () => {
    const cases = build();
    expect(cases.length).toBeGreaterThan(100);
    const text = `${JSON.stringify(cases, null, 1)}\n`;
    if (process.env.UPDATE_GOLDEN === '1') writeFileSync(GOLDEN, text);
    expect(readFileSync(GOLDEN, 'utf8'), 'run UPDATE_GOLDEN=1 npm test -- golden').toBe(text);
  });
});
