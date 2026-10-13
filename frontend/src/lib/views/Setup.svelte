<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Component } from 'svelte';
  import { tDynamic, t } from '../i18n/index.svelte';
  import { router, stepHash } from '../router.svelte';
  import { wizard } from '../stores/wizard.svelte';
  import { firstIncompleteStep, type StepId } from '../wizard/steps';

  // One chunk per step: the wizard is not part of the first paint of the Now view.
  const LOADERS: Record<StepId, () => Promise<{ default: Component }>> = {
    region: () => import('./steps/RegionStep.svelte'),
    energy: () => import('./steps/EnergyStep.svelte'),
    grid: () => import('./steps/GridStep.svelte'),
    pv: () => import('./steps/PvStep.svelte'),
    battery: () => import('./steps/BatteryStep.svelte'),
    tariff: () => import('./steps/TariffStep.svelte'),
    devices: () => import('./steps/DevicesStep.svelte'),
    priorities: () => import('./steps/PrioritiesStep.svelte'),
    mode: () => import('./steps/ModeStep.svelte'),
    summary: () => import('./steps/SummaryStep.svelte'),
  };

  $effect(() => {
    void wizard.ensureLoaded();
  });

  // `#/setup` resumes at the first step that is not done yet.
  $effect(() => {
    if (wizard.loaded && router.step === null) {
      router.replace(stepHash(firstIncompleteStep(wizard.completedSteps)));
    }
  });
</script>

<div class="setup">
  {#if wizard.loadError}
    <p class="notice error" role="alert">
      {tDynamic(wizard.loadError, undefined, 'api.error.unknown')}
    </p>
    <button type="button" class="btn" onclick={() => void wizard.load()}>{t('action.retry')}</button
    >
  {:else if !wizard.loaded}
    <p class="hint" role="status">{t('wizard.loading')}</p>
  {:else if router.step}
    {#key router.step}
      {#await LOADERS[router.step]() then mod}
        {@const Step = mod.default}
        <Step />
      {/await}
    {/key}
  {/if}
</div>
