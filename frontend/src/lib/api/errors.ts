// SPDX-License-Identifier: Apache-2.0
import { ApiError } from './client';
import type { MessageKey } from '../i18n/index.svelte';
import type { ValidationIssue } from './types';

export interface FailedRequest {
  /** Catalogue key (`api.error.*`) to show as the headline. */
  key: string;
  issues: ValidationIssue[];
}

/** An error of the REST client as `{key, issues}` for display; anything else is a network error. */
export function describeError(e: unknown): FailedRequest {
  if (e instanceof ApiError) {
    return {
      key: e.key ?? (e.status === 422 ? 'api.error.validation' : 'api.error.network'),
      issues: e.issues,
    };
  }
  return { key: 'api.error.network', issues: [] };
}

/** Raised by a step when its own checks fail; the shell lists `keys` next to the Save button. */
export class FormError extends Error {
  constructor(readonly keys: readonly MessageKey[]) {
    super(keys.join(', '));
    this.name = 'FormError';
  }
}
