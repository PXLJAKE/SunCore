// SPDX-License-Identifier: Apache-2.0
// 422 issues (`{loc, key, args}`) of a section PUT, matched to the fields they belong to.
import type { ValidationIssue } from '../api/types';

/** `loc` without the leading `body`/`query`/`path`: `['grid', 'power', 'entity_id']`. */
export function locPath(issue: ValidationIssue): (string | number)[] {
  const [first, ...rest] = issue.loc;
  return first === 'body' || first === 'query' || first === 'path' ? rest : [first!, ...rest];
}

/** Issues at or below `prefix` (an issue about `['grid']` also concerns the grid fields). */
export function issuesAt(
  issues: readonly ValidationIssue[],
  ...prefix: (string | number)[]
): ValidationIssue[] {
  return issues.filter((issue) => {
    const path = locPath(issue);
    return prefix.every((part, i) => String(path[i]) === String(part));
  });
}

/** Readable path of an issue for the summary list (`grid / power / entity_id`). */
export function issueLocText(issue: ValidationIssue): string {
  return locPath(issue).join(' / ');
}

/** Issues whose path starts with none of the given prefixes (shown in the summary only). */
export function unclaimed(
  issues: readonly ValidationIssue[],
  prefixes: readonly (readonly (string | number)[])[],
): ValidationIssue[] {
  return issues.filter((issue) => {
    const path = locPath(issue);
    return !prefixes.some((p) => p.every((part, i) => String(path[i]) === String(part)));
  });
}

/** Issues below `prefix`, re-rooted under `body` so a sub-form can match them with `issuesAt`. */
export function scopeIssues(
  issues: readonly ValidationIssue[],
  ...prefix: (string | number)[]
): ValidationIssue[] {
  return issuesAt(issues, ...prefix).map((issue) => ({
    ...issue,
    loc: ['body', ...locPath(issue).slice(prefix.length)],
  }));
}
