// SPDX-License-Identifier: Apache-2.0
// Pure helpers of the diagnostics panel (Settings > Advanced): which parts of the export the
// preview lists, the file name and the serialised text of the download.
import type { Diagnostics } from '../api/types';

export type DiagSection =
  | 'versions'
  | 'architecture'
  | 'connection'
  | 'watchdog'
  | 'planner'
  | 'timeseries'
  | 'configuration'
  | 'mapped_entities'
  | 'decisions'
  | 'logs';

export const DIAG_SECTIONS: readonly DiagSection[] = [
  'versions',
  'architecture',
  'connection',
  'watchdog',
  'planner',
  'timeseries',
  'configuration',
  'mapped_entities',
  'decisions',
  'logs',
];

/** The number of entries of a list section, `null` for an object section. */
export function sectionCount(doc: Diagnostics, section: DiagSection): number | null {
  const value: unknown = doc[section];
  return Array.isArray(value) ? value.length : null;
}

/** The text of one section as shown in the preview (indented, so a person can read it). */
export function sectionText(doc: Diagnostics, section: DiagSection): string {
  return JSON.stringify(doc[section], null, 2);
}

/** The text of the downloaded file: the whole export, indented. */
export function exportText(doc: Diagnostics): string {
  return `${JSON.stringify(doc, null, 2)}\n`;
}

/** `suncore-diagnostics-2026-11-25.json`, from the generation time of the export (UTC date). */
export function exportFileName(doc: Diagnostics): string {
  const day = /^\d{4}-\d{2}-\d{2}/.exec(doc.generated_at)?.[0] ?? 'export';
  return `suncore-diagnostics-${day}.json`;
}

/** Size of the file in bytes (UTF-8), for the label of the preview. */
export function exportBytes(doc: Diagnostics): number {
  return new TextEncoder().encode(exportText(doc)).length;
}
