// SPDX-License-Identifier: Apache-2.0
// App-level REST state: /info and the site config (which nodes the Now view shows).
import { api, ApiError } from '../api/client';
import type { Info, SiteConfig } from '../api/types';

export class AppStore {
  info = $state.raw<Info | null>(null);
  /** `undefined` = not loaded yet, `null` = loaded, nothing configured. */
  site = $state.raw<SiteConfig | null | undefined>(undefined);
  error = $state<string | null>(null);

  async load(): Promise<void> {
    try {
      const [info, site] = await Promise.all([api.info(), api.getSite()]);
      this.info = info;
      this.site = site;
      this.error = null;
    } catch (e) {
      this.error = e instanceof ApiError ? (e.key ?? 'api.error.network') : 'api.error.network';
    }
  }

  async reloadSite(): Promise<void> {
    try {
      this.site = await api.getSite();
    } catch {
      // keep the previous value; the live feed still works
    }
  }
}

export const app = new AppStore();
