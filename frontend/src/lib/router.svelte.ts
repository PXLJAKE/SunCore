// SPDX-License-Identifier: Apache-2.0
// Tiny hash router (`#/`, `#/settings`): independent of the ingress base path.
export type Route = 'now' | 'settings';

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#/, '').split('?')[0] ?? '';
  return path === '/settings' ? 'settings' : 'now';
}

class Router {
  route = $state<Route>(parseRoute(typeof location === 'undefined' ? '' : location.hash));

  listen(): () => void {
    const onChange = () => {
      this.route = parseRoute(location.hash);
    };
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }
}

export const router = new Router();
