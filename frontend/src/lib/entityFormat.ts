// SPDX-License-Identifier: Apache-2.0
// Text for the value of an entity candidate: W/kW as power, % as percent, anything else with its
// own unit. Raw states of non-numeric entities (climate, switch) are shown as they are.
import type { EntityCandidate } from './api/types';
import { t, tNumber, tPercent, tPower } from './i18n/index.svelte';

export function candidateValueText(c: EntityCandidate): string {
  const unit = c.unit.trim();
  if (c.value !== null && c.available) {
    const lower = unit.toLowerCase();
    if (lower === 'w') return tPower(c.value);
    if (lower === 'kw') return tPower(c.value * 1000);
    if (unit === '%') return tPercent(c.value);
    const n = tNumber(c.value, { maximumFractionDigits: 2 });
    return unit === '' ? n : `${n} ${unit}`;
  }
  if (c.available && c.state !== '' && c.state !== 'unknown') return c.state;
  return t('entity.unavailable');
}

/** Domains whose state is a number with a unit; others (climate, switch, …) carry a raw state. */
const MEASURING_DOMAINS = new Set(['sensor', 'number', 'input_number']);

/**
 * Whether unit and numeric quality apply to the candidate. A climate or switch entity has no unit
 * and a text state ("off"), which is normal, not an invalid value.
 */
export function measuring(c: Pick<EntityCandidate, 'entity_id' | 'domain'>): boolean {
  const domain = c.domain || (c.entity_id.split('.', 1)[0] ?? '');
  return MEASURING_DOMAINS.has(domain);
}

/** Search terms must all occur in name, entity id, area or device name (case-insensitive). */
export function matchesQuery(c: EntityCandidate, query: string): boolean {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;
  const hay = [c.name, c.entity_id, c.area_name ?? '', c.device_name ?? ''].join(' ').toLowerCase();
  return terms.every((term) => hay.includes(term));
}

/** The candidate whose entity id or exact friendly name equals `text` (case-insensitive). */
export function resolveCandidate(
  candidates: readonly EntityCandidate[],
  text: string,
): EntityCandidate | undefined {
  const needle = text.trim().toLowerCase();
  if (needle === '') return undefined;
  return (
    candidates.find((c) => c.entity_id.toLowerCase() === needle) ??
    candidates.find((c) => c.name.trim().toLowerCase() === needle)
  );
}

/** Maximum number of options rendered in a picker. */
export const MAX_SUGGESTIONS = 50;

export function filterCandidates(
  candidates: readonly EntityCandidate[],
  query: string,
  cap: number = MAX_SUGGESTIONS,
): { items: EntityCandidate[]; total: number } {
  const matches = candidates.filter((c) => matchesQuery(c, query));
  return { items: matches.slice(0, cap), total: matches.length };
}

/** Catalogue keys of the matched criteria, without duplicates (`translation_key*` look alike). */
export function criterionKeys(c: Pick<EntityCandidate, 'criteria'>): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const key of c.criteria) {
    const base = key.replace(/_pattern$/, '');
    if (!seen.has(base)) {
      seen.add(base);
      out.push(key);
    }
  }
  return out;
}

/** Entity fragment for POST /preview: `battery.power` -> `{battery: {power: ref}}`. */
export function fragmentFor(
  path: string,
  ref: { entity_id: string; invert_sign: boolean },
): Record<string, unknown> {
  const parts = path.split('.');
  const build = (i: number): unknown => {
    const part = parts[i]!;
    const isIndex = /^\d+$/.test(part);
    const inner = i === parts.length - 1 ? ref : build(i + 1);
    if (isIndex) {
      const list: unknown[] = [];
      list[Number(part)] = inner;
      return list;
    }
    return { [part]: inner };
  };
  return build(0) as Record<string, unknown>;
}
