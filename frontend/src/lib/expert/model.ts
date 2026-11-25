// SPDX-License-Identifier: Apache-2.0
// Pure logic of the generated expert form: input parsing, validation against the hard range,
// dirty state, grouping and search. No rendering, no network.
import type { Param, ParamValue, ValidationIssue } from '../api/types';

/** What an input holds while editing: text for numbers/choices/entities, a boolean for switches. */
export type Raw = string | boolean;

export type Parsed =
  | { ok: true; value: ParamValue }
  | { ok: false; key: string; args: Record<string, string | number> };

/** The value of a parameter as the input shows it. */
export function toRaw(p: Param): Raw {
  if (p.type === 'bool') return p.value === true;
  return p.value === null || p.value === undefined ? '' : String(p.value);
}

/** Parse an input; the server stays the authority, this only catches the obvious early. */
export function parseRaw(p: Param, raw: Raw): Parsed {
  if (p.type === 'bool') return { ok: true, value: raw === true };
  const text = String(raw).trim();
  if (p.type === 'enum') {
    if (!p.choices.includes(text)) {
      return {
        ok: false,
        key: 'api.validation.param_not_in_choices',
        args: { param: p.key, choices: p.choices.join(', ') },
      };
    }
    return { ok: true, value: text };
  }
  if (p.type === 'entity') return { ok: true, value: text === '' ? null : text };
  const n = Number(text);
  if (text === '' || !Number.isFinite(n) || (p.type === 'int' && !Number.isInteger(n))) {
    return {
      ok: false,
      key: 'api.validation.param_wrong_type',
      args: { param: p.key, expected: p.type },
    };
  }
  if ((p.minimum !== null && n < p.minimum) || (p.maximum !== null && n > p.maximum)) {
    return {
      ok: false,
      key: 'api.validation.param_out_of_range',
      args: { param: p.key, min: p.minimum ?? '-', max: p.maximum ?? '-' },
    };
  }
  return { ok: true, value: n };
}

export type Drafts = Readonly<Record<string, Raw>>;

/** Whether the draft of `p` differs from its stored value. */
export function isEdited(p: Param, drafts: Drafts): boolean {
  if (!(p.key in drafts)) return false;
  const raw = drafts[p.key] as Raw;
  const parsed = parseRaw(p, raw);
  if (!parsed.ok) return true;
  return parsed.value !== p.value;
}

export interface Plan {
  /** Valid changes to send. */
  changes: Record<string, ParamValue>;
  /** Client-side problems per key (an edited input that is not valid). */
  errors: Record<string, ValidationIssue>;
}

export function planChanges(params: readonly Param[], drafts: Drafts): Plan {
  const changes: Record<string, ParamValue> = {};
  const errors: Record<string, ValidationIssue> = {};
  for (const p of params) {
    if (!isEdited(p, drafts)) continue;
    const parsed = parseRaw(p, drafts[p.key] as Raw);
    if (parsed.ok) changes[p.key] = parsed.value;
    else errors[p.key] = { loc: [p.key], key: parsed.key, args: parsed.args };
  }
  return { changes, errors };
}

export interface SafetyChange {
  key: string;
  from: ParamValue;
  to: ParamValue;
}

/** The safety-tagged keys among `changes`, with old and new value (for the confirmation). */
export function safetyChanges(
  params: readonly Param[],
  changes: Readonly<Record<string, ParamValue>>,
): SafetyChange[] {
  return params
    .filter((p) => p.safety && p.key in changes)
    .map((p) => ({ key: p.key, from: p.value, to: changes[p.key] as ParamValue }));
}

export function needsRestart(params: readonly Param[], changedKeys: readonly string[]): boolean {
  const set = new Set(changedKeys);
  return params.some((p) => p.restart && set.has(p.key));
}

export interface Group {
  name: string;
  params: Param[];
  modified: number;
}

/**
 * Group the parameters in the order the server sent the groups. `visible` decides per parameter
 * (search, "only modified"); empty groups are dropped.
 */
export function groupParams(
  params: readonly Param[],
  groups: readonly string[],
  visible: (p: Param) => boolean,
): Group[] {
  return groups
    .map((name) => {
      const own = params.filter((p) => p.group === name);
      return { name, params: own.filter(visible), modified: own.filter((p) => p.modified).length };
    })
    .filter((g) => g.params.length > 0);
}

/** Case-insensitive match of the query against key, displayed name and help text. */
export function matchesQuery(query: string, ...haystack: string[]): boolean {
  const q = query.trim().toLowerCase();
  if (q === '') return true;
  return haystack.some((h) => h.toLowerCase().includes(q));
}

/** Server issues of one parameter (the 422 names it in `args.param`). */
export function issuesFor(key: string, issues: readonly ValidationIssue[]): ValidationIssue[] {
  return issues.filter((i) => i.args?.param === key || i.loc.includes(key));
}
