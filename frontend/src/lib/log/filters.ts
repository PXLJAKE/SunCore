// SPDX-License-Identifier: Apache-2.0
// The decision log's filters: one plain object, serialised into the page's `#/decisions?...`
// (shareable) and into the `/api/v1/decisions` query. Pure, no DOM.
import type { DecisionKind, DecisionOrigin } from '../api/types';

export const DECISION_KINDS: readonly DecisionKind[] = [
  'accepted',
  'proposed',
  'shadow',
  'held',
  'rejected',
  'failed',
];
export const DECISION_LAYERS: readonly DecisionOrigin[] = [
  'planner',
  'controller',
  'user',
  'safety',
];

export const PAGE_SIZE = 50;

export interface LogFilters {
  /** Empty string = all devices. */
  device: string;
  /** Empty = all outcomes. */
  kinds: DecisionKind[];
  /** Empty = all layers. */
  layer: DecisionOrigin | '';
  /** Reason key or dotted prefix; empty = any. */
  reason: string;
  /** Free text; empty = none. */
  q: string;
  /** Local dates `YYYY-MM-DD`, both inclusive; empty = open. */
  from: string;
  to: string;
}

export const emptyFilters = (): LogFilters => ({
  device: '',
  kinds: [],
  layer: '',
  reason: '',
  q: '',
  from: '',
  to: '',
});

export const REASON_PATTERN = /^[a-z][a-z0-9_.]*$/;
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

const validDate = (s: string): boolean =>
  DATE_PATTERN.test(s) && !Number.isNaN(Date.parse(`${s}T00:00:00Z`));

/** The day after `date` (`YYYY-MM-DD`), calendar arithmetic without a time zone. */
export function nextDay(date: string): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

/** Read filters from a hash query; unknown or invalid values are dropped. */
export function filtersFromParams(params: URLSearchParams): LogFilters {
  const f = emptyFilters();
  f.device = params.get('device') ?? '';
  const kinds = params
    .getAll('kind')
    .flatMap((k) => k.split(','))
    .filter((k): k is DecisionKind => (DECISION_KINDS as readonly string[]).includes(k));
  f.kinds = [...new Set(kinds)];
  const layer = params.get('layer') ?? '';
  f.layer = (DECISION_LAYERS as readonly string[]).includes(layer) ? (layer as DecisionOrigin) : '';
  const reason = params.get('reason') ?? '';
  f.reason = REASON_PATTERN.test(reason) ? reason : '';
  f.q = (params.get('q') ?? '').slice(0, 100);
  const from = params.get('from') ?? '';
  const to = params.get('to') ?? '';
  f.from = validDate(from) ? from : '';
  f.to = validDate(to) ? to : '';
  return f;
}

/** The shareable form of the filters (only what is set, in a fixed order). */
export function filtersToParams(f: LogFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.device) p.set('device', f.device);
  for (const k of f.kinds) p.append('kind', k);
  if (f.layer) p.set('layer', f.layer);
  if (f.reason) p.set('reason', f.reason);
  if (f.q) p.set('q', f.q);
  if (f.from) p.set('from', f.from);
  if (f.to) p.set('to', f.to);
  return p;
}

/** Problems the server would reject (shown next to the field before asking). */
export function filterProblems(f: LogFilters): { reason: boolean; range: boolean } {
  return {
    reason: f.reason !== '' && !REASON_PATTERN.test(f.reason),
    range: f.from !== '' && f.to !== '' && f.to < f.from,
  };
}

/** The API query: dates are local to the site (the server reads a bare date in its zone). */
export function filtersToApi(
  f: LogFilters,
  page: { limit?: number; offset?: number } = {},
): URLSearchParams {
  const p = new URLSearchParams();
  if (f.device) p.set('device_id', f.device);
  for (const k of f.kinds) p.append('kind', k);
  if (f.layer) p.set('layer', f.layer);
  if (f.reason) p.set('reason', f.reason);
  if (f.q) p.set('q', f.q);
  if (f.from) p.set('from', f.from);
  if (f.to) p.set('to', nextDay(f.to)); // `to` is exclusive on the server
  if (page.limit !== undefined) p.set('limit', String(page.limit));
  if (page.offset) p.set('offset', String(page.offset));
  return p;
}

/** Whether any filter is set. */
export const hasFilters = (f: LogFilters): boolean => filtersToParams(f).toString() !== '';

/** The hash of the log with these filters, e.g. `#/decisions?device=ac&kind=shadow`. */
export function logHash(f: Partial<LogFilters>): string {
  const text = filtersToParams({ ...emptyFilters(), ...f }).toString();
  return text ? `#/decisions?${text}` : '#/decisions';
}
