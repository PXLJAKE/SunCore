// SPDX-License-Identifier: Apache-2.0
// Wizard state: the stored sections and one form per section. Each step edits its slice of a
// form and saves the section with PUT; `wizard.completed_steps` makes the wizard resumable.
import { api, controlApi } from '../api/client';
import { describeError, FormError, type FailedRequest } from '../api/errors';
import type {
  DeviceConfig,
  DevicesConfig,
  ForecastConfig,
  GoalsConfig,
  OperatingMode,
  OperationConfig,
  RegionConfig,
  SiteConfig,
  TariffConfig,
  WizardState,
} from '../api/types';
import { applyLanguage, type MessageKey } from '../i18n/index.svelte';
import {
  forecastFormFromConfig,
  forecastFromForm,
  type ForecastForm,
} from '../wizard/forecastForm';
import {
  goalsFormFromConfig,
  goalsFromForm,
  operationFormFromConfig,
  operationFromForm,
  type GoalsForm,
  type OperationForm,
} from '../wizard/goalsForm';
import { dirtySections, type DirtySection } from '../wizard/dirty';
import { regionFormFromConfig, regionFromForm, type RegionForm } from '../wizard/regionForm';
import { emptySiteForm, siteFormFromSite, siteFromForm, type SiteForm } from '../wizard/siteForm';
import { STEP_IDS, withCompleted, type StepId } from '../wizard/steps';
import {
  isTariffComplete,
  tariffFormFromConfig,
  tariffFromForm,
  type TariffForm,
} from '../wizard/tariffForm';
import { app } from './app.svelte';

export const WIZARD_VERSION = 1;

/** Result state of a save: used by the shell to show progress and errors. */
export class SaveState {
  saving = $state(false);
  error = $state<string | null>(null);
  issues = $state.raw<FailedRequest['issues']>([]);
  /** Messages of the step's own checks (`FormError`). */
  messages = $state.raw<readonly MessageKey[]>([]);

  clear(): void {
    this.error = null;
    this.issues = [];
    this.messages = [];
  }

  /** Run `fn`; returns `true` on success, otherwise stores the error for display. */
  async run(fn: () => Promise<void> | void): Promise<boolean> {
    this.saving = true;
    this.clear();
    try {
      await fn();
      return true;
    } catch (e) {
      if (e instanceof FormError) {
        this.error = 'wizard.error.check_input';
        this.messages = e.keys;
        return false;
      }
      const failed = describeError(e);
      this.error = failed.key;
      this.issues = failed.issues;
      return false;
    } finally {
      this.saving = false;
    }
  }
}

export class WizardStore {
  loaded = $state(false);
  loadError = $state<string | null>(null);

  /** Stored documents as last seen on the server (for summaries and the overview). */
  saved = $state.raw<{
    site: SiteConfig | null;
    region: RegionConfig | null;
    tariff: TariffConfig | null;
    forecast: ForecastConfig | null;
    devices: DevicesConfig | null;
    goals: GoalsConfig | null;
    operation: OperationConfig | null;
    wizard: WizardState | null;
  }>({
    site: null,
    region: null,
    tariff: null,
    forecast: null,
    devices: null,
    goals: null,
    operation: null,
    wizard: null,
  });

  site = $state<SiteForm>(emptySiteForm());
  region = $state<RegionForm>(regionFormFromConfig(null));
  tariff = $state<TariffForm>(tariffFormFromConfig(null));
  forecast = $state<ForecastForm>(forecastFormFromConfig(null));
  goals = $state<GoalsForm>(goalsFormFromConfig(null));
  operation = $state<OperationForm>(operationFormFromConfig(null));
  devices = $state.raw<DeviceConfig[]>([]);
  completedSteps = $state.raw<string[]>([]);
  completed = $state(false);

  private loading: Promise<void> | null = null;

  /** Load once; later calls return the same state until `invalidate()`. */
  ensureLoaded(): Promise<void> {
    if (this.loaded) return Promise.resolve();
    this.loading ??= this.load().finally(() => (this.loading = null));
    return this.loading;
  }

  invalidate(): void {
    this.loaded = false;
  }

  async load(): Promise<void> {
    try {
      const [site, region, tariff, forecast, devices, goals, operation, wizard] = await Promise.all(
        [
          api.getSite(),
          api.getSection<RegionConfig>('region'),
          api.getSection<TariffConfig>('tariff'),
          api.getSection<ForecastConfig>('forecast'),
          api.getSection<DevicesConfig>('devices'),
          api.getSection<GoalsConfig>('goals'),
          api.getSection<OperationConfig>('operation'),
          api.getSection<WizardState>('wizard'),
        ],
      );
      this.saved = { site, region, tariff, forecast, devices, goals, operation, wizard };
      this.site = siteFormFromSite(site);
      this.region = regionFormFromConfig(region);
      this.tariff = tariffFormFromConfig(tariff);
      this.forecast = forecastFormFromConfig(forecast);
      this.goals = goalsFormFromConfig(goals);
      this.operation = operationFormFromConfig(operation);
      this.devices = devices?.devices ?? [];
      this.completedSteps = wizard?.completed_steps ?? [];
      this.completed = wizard?.completed ?? false;
      this.loadError = null;
      this.loaded = true;
    } catch (e) {
      this.loadError = describeError(e).key;
    }
  }

  /** Sections with edits that are not stored yet (reads the forms, so it is reactive). */
  dirty(): DirtySection[] {
    if (!this.loaded) return [];
    const { site, region, tariff, forecast, goals, operation } = this.saved;
    return dirtySections({
      forms: {
        site: this.site,
        region: this.region,
        tariff: this.tariff,
        forecast: this.forecast,
        goals: this.goals,
        operation: this.operation,
      },
      saved: { site, region, tariff, forecast, goals, operation },
      deviceIds: this.devices.map((d) => d.id),
    });
  }

  /** Throw away unsaved edits: the forms are filled from the server again. */
  async discard(): Promise<void> {
    this.invalidate();
    await this.ensureLoaded();
  }

  // --- saves: each throws ApiError (422 carries the issues) --------------------------

  async saveRegion(): Promise<void> {
    const saved = await api.putSection<RegionConfig>('region', regionFromForm(this.region));
    this.saved = { ...this.saved, region: saved };
    this.region = regionFormFromConfig(saved);
    app.region = saved;
    applyLanguage(saved.language);
  }

  async saveSite(): Promise<void> {
    const saved = await api.putSite(siteFromForm(this.site));
    this.saved = { ...this.saved, site: saved };
    this.site = siteFormFromSite(saved);
    await app.reloadSite();
  }

  /** Store the tariff section; returns `false` (and stores nothing) while it is incomplete. */
  async saveTariff(): Promise<boolean> {
    if (!isTariffComplete(this.tariff)) return false;
    const saved = await api.putSection<TariffConfig>('tariff', tariffFromForm(this.tariff));
    this.saved = { ...this.saved, tariff: saved };
    this.tariff = tariffFormFromConfig(saved);
    return true;
  }

  /** Store the forecast sources; nothing is written while there is nothing to keep. */
  async saveForecast(): Promise<void> {
    if (this.forecast.templateIds.length === 0 && this.saved.forecast === null) return;
    const saved = await api.putSection<ForecastConfig>('forecast', forecastFromForm(this.forecast));
    this.saved = { ...this.saved, forecast: saved };
    this.forecast = forecastFormFromConfig(saved);
  }

  async saveDevices(devices: DeviceConfig[]): Promise<void> {
    const saved = await api.putSection<DevicesConfig>('devices', {
      schema_version: 1,
      devices,
    });
    this.saved = { ...this.saved, devices: saved };
    this.devices = saved.devices ?? [];
    // Devices that left must not linger in the stored operating modes.
    this.operation = {
      ...this.operation,
      deviceModes: Object.fromEntries(
        Object.entries(this.operation.deviceModes).filter(([id]) =>
          (saved.devices ?? []).some((d) => d.id === id),
        ),
      ),
    };
  }

  async saveGoals(): Promise<void> {
    const saved = await api.putSection<GoalsConfig>('goals', goalsFromForm(this.goals));
    this.saved = { ...this.saved, goals: saved };
    this.goals = goalsFormFromConfig(saved);
  }

  async saveOperation(): Promise<void> {
    const body = operationFromForm(
      this.operation,
      this.devices.map((d) => d.id),
    );
    // The full document may only keep or lower modes; raising goes through the mode
    // endpoints, and a device above shadow needs its identity token from the control state.
    const current = this.saved.operation;
    const currentModes: Record<string, OperatingMode> = current?.device_modes ?? {};
    const wanted: Record<string, OperatingMode> = body.device_modes ?? {};
    let saved: OperationConfig | null = current;
    if (body.mode !== (current?.mode ?? 'shadow')) {
      saved = await controlApi.setGlobalMode(body.mode ?? 'shadow');
    }
    let identities: Record<string, string> | null = null;
    const ids = [...Object.keys(currentModes), ...Object.keys(wanted)];
    for (const id of ids.filter((x, i) => ids.indexOf(x) === i)) {
      const want = wanted[id] ?? 'shadow';
      if (want === (currentModes[id] ?? 'shadow')) continue;
      if (want !== 'shadow' && identities === null) {
        const state = await controlApi.state();
        identities = Object.fromEntries(state.devices.map((d) => [d.device_id, d.identity]));
      }
      const identity = want === 'shadow' ? null : (identities?.[id] ?? null);
      saved = await controlApi.setDeviceMode(id, want, identity);
    }
    saved ??= await api.putSection<OperationConfig>('operation', body);
    this.saved = { ...this.saved, operation: saved };
    this.operation = operationFormFromConfig(saved);
  }

  // --- progress ------------------------------------------------------------------------

  private async putWizard(completedSteps: string[], completed: boolean): Promise<void> {
    const saved = await api.putSection<WizardState>('wizard', {
      schema_version: 1,
      completed,
      completed_steps: completedSteps,
      version: WIZARD_VERSION,
    });
    this.saved = { ...this.saved, wizard: saved };
    this.completedSteps = saved.completed_steps ?? [];
    this.completed = saved.completed;
    app.wizard = saved;
  }

  async markDone(step: StepId): Promise<void> {
    if (this.completedSteps.includes(step)) return;
    await this.putWizard(withCompleted(this.completedSteps, step), this.completed);
  }

  async finish(): Promise<void> {
    await this.putWizard([...STEP_IDS], true);
  }

  /** Start over: the steps are shown as not done, the stored configuration stays. */
  async restart(): Promise<void> {
    await this.putWizard([], false);
  }
}

export const wizard = new WizardStore();
