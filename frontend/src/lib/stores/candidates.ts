// SPDX-License-Identifier: Apache-2.0
// Entity candidates per role, cached for a short time: the backend serves them from its own
// cache, and every picker of a step asks for its role once per visit.
import { api } from '../api/client';
import type { EntityCandidate } from '../api/types';

export interface CandidateQuery {
  /** Site role (`grid_power`) or, with `template`, a template role. */
  role: string;
  template?: string;
  deviceId?: string | null;
  /** Name of the device already chosen: candidates of a same-named device rank first. */
  deviceName?: string | null;
  /** Also hidden, diagnostic and config entities (ranked lower by the backend). */
  includeHidden?: boolean;
}

const MAX_AGE_MS = 20_000;
const cache = new Map<string, { at: number; promise: Promise<EntityCandidate[]> }>();

export const queryKey = (q: CandidateQuery): string =>
  `${q.template ?? ''}|${q.role}|${q.deviceId ?? ''}|${q.deviceName ?? ''}|${q.includeHidden ? 'all' : ''}`;

export function loadCandidates(
  q: CandidateQuery,
  now: number = Date.now(),
): Promise<EntityCandidate[]> {
  const key = queryKey(q);
  const hit = cache.get(key);
  if (hit && now - hit.at < MAX_AGE_MS) return hit.promise;
  const promise = q.template
    ? api.entitiesForTemplate(
        q.template,
        q.role,
        q.deviceId ?? undefined,
        q.includeHidden === true,
        q.deviceName ?? undefined,
      )
    : api.entitiesForRole(q.role, q.includeHidden === true);
  cache.set(key, { at: now, promise });
  // A failed request must not be served from the cache again.
  promise.catch(() => {
    if (cache.get(key)?.promise === promise) cache.delete(key);
  });
  return promise;
}

export function clearCandidateCache(): void {
  cache.clear();
}
