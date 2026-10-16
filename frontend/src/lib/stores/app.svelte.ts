// SPDX-License-Identifier: Apache-2.0
// App-level REST state: /info, the site config (which nodes the Now view shows), the region
// (language) and the wizard state (first start redirect).
import { api, ApiError } from '../api/client';
import type { DevicesConfig, Info, RegionConfig, SiteConfig, WizardState } from '../api/types';
import { applyLanguage } from '../i18n/index.svelte';

export class AppStore {
  info = $state.raw<Info | null>(null);
  /** `undefined` = not loaded yet, `null` = loaded, nothing configured. */
  site = $state.raw<SiteConfig | null | undefined>(undefined);
  /** `undefined` = not loaded yet, `null` = the wizard was never started. */
  wizard = $state.raw<WizardState | null | undefined>(undefined);
  region = $state.raw<RegionConfig | null>(null);
  /** Configured devices (units of adjustable loads on the cards). */
  devices = $state.raw<DevicesConfig | null>(null);
  error = $state<string | null>(null);

  async load(): Promise<void> {
    try {
      const [info, site, wizard, region, devices] = await Promise.all([
        api.info(),
        api.getSite(),
        api.getSection<WizardState>('wizard'),
        api.getSection<RegionConfig>('region'),
        api.getSection<DevicesConfig>('devices'),
      ]);
      this.info = info;
      this.site = site;
      this.wizard = wizard;
      this.region = region;
      this.devices = devices;
      if (region) applyLanguage(region.language);
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

  /** The wizard has to be started: no state stored yet, or not completed. */
  get needsSetup(): boolean {
    return this.wizard !== undefined && !(this.wizard?.completed ?? false);
  }
}

export const app = new AppStore();
