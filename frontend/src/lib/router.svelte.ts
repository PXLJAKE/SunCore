// SPDX-License-Identifier: Apache-2.0
// Tiny hash router (`#/`, `#/plan`, `#/settings`, `#/decisions`, `#/setup[/<step>]`): independent of the ingress base path.
import { isStepId, type StepId } from './wizard/steps';

export type Route = 'now' | 'plan' | 'settings' | 'setup' | 'decisions';

export interface Location {
  route: Route;
  /** Only for `setup`: the step, `null` for `#/setup` (resume where the user stopped). */
  step: StepId | null;
}

export function parseLocation(hash: string): Location {
  const path = hash.replace(/^#/, '').split('?')[0] ?? '';
  if (path === '/plan') return { route: 'plan', step: null };
  if (path === '/decisions') return { route: 'decisions', step: null };
  if (path === '/settings') return { route: 'settings', step: null };
  if (path === '/setup' || path === '/setup/') return { route: 'setup', step: null };
  if (path.startsWith('/setup/')) {
    const id = path.slice('/setup/'.length).replace(/\/$/, '');
    return isStepId(id) ? { route: 'setup', step: id } : { route: 'setup', step: null };
  }
  return { route: 'now', step: null };
}

/** The hash that opens a step. */
export const stepHash = (id: StepId): string => `#/setup/${id}`;

class Router {
  private current = $state<Location>(
    parseLocation(typeof location === 'undefined' ? '' : location.hash),
  );

  get route(): Route {
    return this.current.route;
  }

  get step(): StepId | null {
    return this.current.step;
  }

  listen(): () => void {
    const onChange = () => {
      this.current = parseLocation(location.hash);
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
  }
}

export const router = new Router();
