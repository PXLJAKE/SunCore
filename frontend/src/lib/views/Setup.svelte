<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import type { Component } from 'svelte';
  import { tDynamic, t } from '../i18n/index.svelte';
  import { router, stepHash } from '../router.svelte';
  import { wizard } from '../stores/wizard.svelte';
  import { firstIncompleteStep, stageOf, type StepId } from '../wizard/steps';
  import { STAGE_LOADERS } from './steps/stages';

  // One chunk per stage: the wizard is not part of the first paint of the Now view.
  async function load(step: StepId): Promise<Component> {
    const mod = await STAGE_LOADERS[stageOf(step).id]();
    return mod[step]!;
  }

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
      {#await load(router.step) then Step}
        <Step />
      {/await}
    {/key}
  {/if}
</div>
