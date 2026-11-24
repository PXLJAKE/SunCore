// SPDX-License-Identifier: Apache-2.0
// Tiny hash router (`#/`, `#/plan`, `#/settings`, `#/decisions`, `#/history`, `#/setup[/<step>]`): independent of the ingress base path.
import { isStepId, type StepId } from './wizard/steps';

export type Route = 'now' | 'plan' | 'settings' | 'setup' | 'decisions' | 'history';

export interface Location {
  route: Route;
  /** Only for `setup`: the step, `null` for `#/setup` (resume where the user stopped). */
  step: StepId | null;
}

export function parseLocation(hash: string): Location {
  const path = hash.replace(/^#/, '').split('?')[0] ?? '';
  if (path === '/plan') return { route: 'plan', step: null };
  if (path === '/history') return { route: 'history', step: null };
  if (path === '/decisions') return { route: 'decisions', step: null };
  if (path === '/settings') return { route: 'settings', step: null };
  if (path === '/setup' || path === '/setup/') return { route: 'setup', step: null };
  if (path.startsWith('/setup/')) {
    const id = path.slice('/setup/'.length).replace(/\/$/, '');
    return isStepId(id) ? { route: 'setup', step: id } : { route: 'setup', step: null };
  }
  return { route: 'now', step: null };
}

/** The part after `?` of a hash. */
export const queryOf = (hash: string): string => hash.split('?')[1] ?? '';

/** The hash that opens a step. */
export const stepHash = (id: StepId): string => `#/setup/${id}`;

class Router {
  private current = $state<Location>(
    parseLocation(typeof location === 'undefined' ? '' : location.hash),
  );
  private search = $state(typeof location === 'undefined' ? '' : queryOf(location.hash));

  /** The query of the current hash (`#/history?period=week`), already parsed. */
  get query(): URLSearchParams {
    return new URLSearchParams(this.search);
  }

  get route(): Route {
    return this.current.route;
  }

  get step(): StepId | null {
    return this.current.step;
  }

  listen(): () => void {
    const onChange = () => {
      this.current = parseLocation(location.hash);
      this.search = queryOf(location.hash);
    };
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }

  /** Navigate (adds a history entry); `hash` like `#/setup/grid`. */
  go(hash: string): void {
    if (location.hash === hash) return;
    location.hash = hash;
  }

  /** Navigate without adding a history entry (redirects). */
  replace(hash: string): void {
    history.replaceState(null, '', hash);
    this.current = parseLocation(location.hash);
    this.search = queryOf(location.hash);
  }
}

export const router = new Router();
