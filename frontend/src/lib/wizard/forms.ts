// SPDX-License-Identifier: Apache-2.0
// Shared form primitives of the wizard.
import type { EntityRef } from '../api/types';

export interface RefField {
  entity_id: string;
  invert_sign: boolean;
}

export const emptyRef = (): RefField => ({ entity_id: '', invert_sign: false });

export const fromRef = (r: EntityRef | null | undefined): RefField =>
  r ? { entity_id: r.entity_id, invert_sign: r.invert_sign ?? false } : emptyRef();

/** A reference to send, or `null` while the field is empty. */
export const toRef = (f: RefField, allowInvert = true): EntityRef | null => {
  const id = f.entity_id.trim();
  return id === '' ? null : { entity_id: id, invert_sign: allowInvert ? f.invert_sign : false };
};

/** Number inputs bind `number | null | undefined` depending on the browser state. */
export const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null;

/** Slug for an id: lowercase ascii, digits and `_`, starts with a letter, at most 48 characters. */
export function slugify(text: string): string {
  const folded = text
    .normalize('NFKD')
    .replace(/ß/g, 'ss')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  const slug = /^[a-z]/.test(folded) ? folded : folded === '' ? 'device' : `d_${folded}`;
  return slug.slice(0, 48).replace(/_+$/, '') || 'device';
}

/** `base`, or `base_2`, `base_3`, ... that is not in `taken` (stays within 48 characters). */
export function uniqueId(base: string, taken: ReadonlySet<string>): string {
  if (!taken.has(base)) return base;
  for (let n = 2; ; n += 1) {
    const suffix = `_${n}`;
    const candidate = `${base.slice(0, 48 - suffix.length)}${suffix}`;
    if (!taken.has(candidate)) return candidate;
  }
}
