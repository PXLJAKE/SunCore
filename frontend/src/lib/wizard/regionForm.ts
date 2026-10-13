// SPDX-License-Identifier: Apache-2.0
// Region section <-> form, and the defaults a region template brings.
import type { RegionConfig, RegionTemplate } from '../api/types';

export type Language = RegionConfig['language'];

export interface RegionForm {
  regionId: string;
  currency: string;
  timezone: string;
  language: Language;
}

/** Last-resort defaults when neither the template nor the browser knows better. */
export const FALLBACK_CURRENCY = 'EUR';
export const FALLBACK_TIMEZONE = 'UTC';

export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || FALLBACK_TIMEZONE;
  } catch {
    return FALLBACK_TIMEZONE;
  }
}

export function defaultRegionForm(timezone: string = browserTimezone()): RegionForm {
  return { regionId: 'generic', currency: FALLBACK_CURRENCY, timezone, language: 'auto' };
}

export function regionFormFromConfig(cfg: RegionConfig | null, timezone?: string): RegionForm {
  if (!cfg) return defaultRegionForm(timezone);
  return {
    regionId: cfg.region_id,
    currency: cfg.currency,
    timezone: cfg.timezone,
    language: cfg.language ?? 'auto',
  };
}

export function regionFromForm(form: RegionForm): RegionConfig {
  return {
    schema_version: 1,
    region_id: form.regionId.trim(),
    currency: form.currency.trim().toUpperCase(),
    timezone: form.timezone.trim(),
    language: form.language,
  };
}

/**
 * Choosing a region template fills currency and time zone from it. A template without a value
 * (the generic profile) keeps what is there: the browser's time zone is the best guess left.
 */
export function applyRegionTemplate(form: RegionForm, template: RegionTemplate | undefined): void {
  if (!template) return;
  form.regionId = template.id;
  if (template.currency) form.currency = template.currency;
  if (template.timezone) form.timezone = template.timezone;
}

export function validateRegionForm(form: RegionForm): ('currency' | 'timezone' | 'region')[] {
  const errors: ('currency' | 'timezone' | 'region')[] = [];
  if (form.regionId.trim() === '') errors.push('region');
  if (!/^[A-Za-z]{3}$/.test(form.currency.trim())) errors.push('currency');
  if (form.timezone.trim() === '') errors.push('timezone');
  return errors;
}
