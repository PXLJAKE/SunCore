// SPDX-License-Identifier: Apache-2.0
// One-line summaries of the stored setup for the settings pages (plan phase-7 WP5): each row
// names a wizard step and opens it in edit mode. Pure, so the texts are testable without a view.
import { t, tNumber } from '../i18n/index.svelte';
import type { SettingsPage } from '../router.svelte';
import type { WizardStore } from '../stores/wizard.svelte';
import type { StepId } from '../wizard/steps';

export type Saved = WizardStore['saved'];

export interface StepRow {
  step: StepId;
  text: string;
}

/** The wizard steps each settings page lists, in order; the other pages have none. */
export const PAGE_STEPS: Partial<Record<SettingsPage, readonly StepId[]>> = {
  system: ['region', 'energy', 'grid', 'pv', 'battery', 'tariff'],
  devices: ['devices'],
  goals: ['priorities', 'mode', 'summary'],
};

function pvText(s: Saved): string {
  const pv = s.site?.pv ?? [];
  const kwp =
    pv.length > 0 && pv.every((a) => a.kwp != null)
      ? pv.reduce((sum, a) => sum + (a.kwp ?? 0), 0)
      : null;
  const arrays =
    kwp == null
      ? t('settings.sum.pv', { count: pv.length })
      : t('settings.sum.pv_kwp', {
          count: pv.length,
          kwp: tNumber(kwp, { maximumFractionDigits: 2 }),
        });
  return `${arrays} · ${t('settings.sum.forecast', { count: s.forecast?.sources?.length ?? 0 })}`;
}

function batteryText(s: Saved): string {
  const battery = s.site?.battery;
  if (!battery) return t('settings.sum.battery_none');
  const control = s.battery?.adapter_template_id
    ? t('settings.sum.battery_control', { adapter: s.battery.adapter_template_id })
    : t('settings.sum.battery_observe');
  const plan = s.battery?.model
    ? ` · ${t('settings.sum.battery_plan', {
        strategy: t(`bat.strategy.${s.battery.strategy ?? 'self_consumption'}`),
        risk: t(`bat.risk.${s.battery.risk ?? 'cautious'}`),
      })}`
    : '';
  return `${t('settings.sum.battery', { entity: battery.power.entity_id })} · ${control}${plan}`;
}

/** The summary of one step, from the stored (not the edited) configuration. */
export function stepSummary(step: StepId, s: Saved): string {
  const none = t('settings.sum.none');
  const site = s.site;
  switch (step) {
    case 'region':
      return s.region
        ? t('settings.sum.region', {
            region: s.region.region_id,
            currency: s.region.currency,
            tz: s.region.timezone,
          })
        : none;
    case 'energy':
      return t('settings.sum.energy');
    case 'grid':
      return site
        ? t('settings.sum.grid', {
            kind: site.grid.power ? 'single' : 'pair',
            entity: site.grid.power?.entity_id ?? site.grid.import_power?.entity_id ?? '',
          })
        : none;
    case 'pv':
      return site ? pvText(s) : none;
    case 'battery':
      return site ? batteryText(s) : none;
    case 'tariff':
      return s.tariff
        ? t('settings.sum.tariff', { imp: s.tariff.import.kind, exp: s.tariff.export.kind })
        : none;
    case 'devices':
      return t('settings.sum.devices', { count: s.devices?.devices?.length ?? 0 });
    case 'priorities':
      return s.goals ? t('settings.sum.goals') : none;
    case 'mode':
      return s.operation ? t(`mode.${s.operation.mode}`) : t('settings.sum.mode_default');
    case 'summary':
      return t('settings.sum.check');
  }
}

export function pageRows(page: SettingsPage, s: Saved): StepRow[] {
  return (PAGE_STEPS[page] ?? []).map((step) => ({ step, text: stepSummary(step, s) }));
}
