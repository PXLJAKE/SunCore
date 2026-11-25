// SPDX-License-Identifier: Apache-2.0
// Tiny hash router (`#/`, `#/plan`, `#/settings[/<page>]`, `#/decisions`, `#/history`, `#/whatif`, `#/setup[/<step>]`): independent of the ingress base path.
import { SvelteURLSearchParams } from 'svelte/reactivity';
import { isStepId, type StepId } from './wizard/steps';

export type Route = 'now' | 'plan' | 'settings' | 'setup' | 'decisions' | 'history' | 'whatif';

/** The four destinations of the navigation (ADR-0019); several routes can share one. */
export type Destination = 'now' | 'plan' | 'analysis' | 'settings';

/** The destination a route belongs to: *Analysis* holds history, decisions and what-if. */
export function destinationOf(route: Route): Destination {
  switch (route) {
    case 'plan':
      return 'plan';
    case 'history':
    case 'decisions':
    case 'whatif':
      return 'analysis';
    case 'settings':
    case 'setup':
      return 'settings';
    default:
      return 'now';
  }
}

/** The sub-pages of *Settings* (`#/settings/<page>`), plan phase-7 WP5. */
export const SETTINGS_PAGES = ['system', 'devices', 'goals', 'ha', 'advanced'] as const;
export type SettingsPage = (typeof SETTINGS_PAGES)[number];

export function isSettingsPage(value: string): value is SettingsPage {
  return (SETTINGS_PAGES as readonly string[]).includes(value);
}

export interface Location {
  route: Route;
  /** Only for `setup`: the step, `null` for `#/setup` (resume where the user stopped). */
  step: StepId | null;
  /** Only for `settings`: the sub-page, `null` for the index (`#/settings`). */
  page?: SettingsPage | null;
}

export function parseLocation(hash: string): Location {
  const path = hash.replace(/^#/, '').split('?')[0] ?? '';
  if (path === '/plan') return { route: 'plan', step: null };
  if (path === '/history') return { route: 'history', step: null };
  if (path === '/whatif') return { route: 'whatif', step: null };
  if (path === '/decisions') return { route: 'decisions', step: null };
  if (path === '/settings' || path === '/settings/') {
    return { route: 'settings', step: null, page: null };
  }
  if (path.startsWith('/settings/')) {
    const id = path.slice('/settings/'.length).replace(/\/$/, '');
    return { route: 'settings', step: null, page: isSettingsPage(id) ? id : null };
  }
  if (path === '/setup' || path === '/setup/') return { route: 'setup', step: null };
  if (path.startsWith('/setup/')) {
    const id = path.slice('/setup/'.length).replace(/\/$/, '');
    return isStepId(id) ? { route: 'setup', step: id } : { route: 'setup', step: null };
  }
  return { route: 'now', step: null };
}

/** The query string of a hash (`#/decisions?device=ac` -> `device=ac`), without the `?`. */
export function hashQuery(hash: string): URLSearchParams {
  const i = hash.indexOf('?');
  return new SvelteURLSearchParams(i < 0 ? '' : hash.slice(i + 1));
}

/** The hash of a settings sub-page. */
export const settingsHash = (page: SettingsPage): string => `#/settings/${page}`;

/** The hash that opens a step. */
export const stepHash = (id: StepId): string => `#/setup/${id}`;

class Router {
  private current = $state<Location>(
    parseLocation(typeof location === 'undefined' ? '' : location.hash),
  );
  /** The current hash, so views can read their `?query` reactively. */
  private hash = $state(typeof location === 'undefined' ? '' : location.hash);

  get query(): URLSearchParams {
    return hashQuery(this.hash);
  }

  get route(): Route {
    return this.current.route;
  }

  get step(): StepId | null {
    return this.current.step;
  }

  /** The settings sub-page, `null` on the index and outside *Settings*. */
  get page(): SettingsPage | null {
    return this.current.page ?? null;
  }

  listen(): () => void {
    const onChange = () => {
      this.current = parseLocation(location.hash);
      this.hash = location.hash;
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
    this.hash = location.hash;
  }
}

export const router = new Router();
