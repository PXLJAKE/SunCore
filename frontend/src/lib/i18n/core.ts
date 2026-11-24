// SPDX-License-Identifier: Apache-2.0
// Framework-free i18n core: locale detection and ICU message rendering.
import { IntlMessageFormat } from 'intl-messageformat';

export const LOCALES = ['en', 'de'] as const;
export type Locale = (typeof LOCALES)[number];
export const FALLBACK_LOCALE: Locale = 'en';

export type Catalogue = Readonly<Record<string, string>>;
export type MessageArgs = Record<string, string | number | boolean | Date | null | undefined>;

/** `de`, `de-AT`, `de_CH` -> `de`; anything else -> `en`. Accepts a list (navigator.languages). */
export function resolveLocale(languages: string | readonly string[] | undefined | null): Locale {
  const list = typeof languages === 'string' ? [languages] : (languages ?? []);
  for (const raw of list) {
    const base = raw.toLowerCase().split(/[-_]/)[0];
    const hit = LOCALES.find((l) => l === base);
    if (hit) return hit;
  }
  return FALLBACK_LOCALE;
}

export interface Translator {
  /** Render `key` in `locale`; falls back to English, then to the key itself. */
  render(locale: Locale, key: string, args?: MessageArgs): string;
  has(key: string, locale?: Locale): boolean;
  /** Whether the catalogue of `locale` is loaded (English always is). */
  loaded(locale: Locale): boolean;
  /** Add or replace the main catalogue of a locale (lazy loading; drops its compiled cache). */
  set(locale: Locale, catalogue: Catalogue): void;
  /** Add messages of a lazy side catalogue (param texts); it works before and after `set`, and
   * keys of the main catalogue win. */
  extend(locale: Locale, messages: Catalogue): void;
}

/** English is the fallback and must be present; other locales may arrive later via `set`. */
export function createTranslator(
  initial: Partial<Record<Locale, Catalogue>> & { en: Catalogue },
): Translator {
  const catalogues: Partial<Record<Locale, Catalogue>> = { ...initial };
  const extras: Partial<Record<Locale, Catalogue>> = {};
  const lookup = (locale: Locale, key: string): string | undefined => {
    const main = catalogues[locale];
    if (main && Object.hasOwn(main, key)) return main[key];
    const extra = extras[locale];
    return extra && Object.hasOwn(extra, key) ? extra[key] : undefined;
  };
  const cache = new Map<string, IntlMessageFormat>();

  const compile = (locale: Locale, key: string): IntlMessageFormat | null => {
    const id = `${locale}\u0000${key}`;
    const hit = cache.get(id);
    if (hit) return hit;
    const source = lookup(locale, key);
    if (source === undefined) return null;
    try {
      const fmt = new IntlMessageFormat(source, locale);
      cache.set(id, fmt);
      return fmt;
    } catch {
      return null;
    }
  };

  return {
    render(locale, key, args) {
      for (const l of locale === FALLBACK_LOCALE ? [locale] : [locale, FALLBACK_LOCALE]) {
        const fmt = compile(l, key);
        if (!fmt) continue;
        try {
          return String(fmt.format(args as Record<string, never>));
        } catch {
          // A missing argument must not blank the UI: try the fallback, then the key.
        }
      }
      return key;
    },
    has(key, locale = FALLBACK_LOCALE) {
      return lookup(locale, key) !== undefined;
    },
    loaded: (locale) => catalogues[locale] !== undefined,
    set(locale, catalogue) {
      catalogues[locale] = catalogue;
      for (const id of [...cache.keys()]) if (id.startsWith(`${locale}\u0000`)) cache.delete(id);
    },
    extend(locale, messages) {
      extras[locale] = { ...extras[locale], ...messages };
    },
  };
}
