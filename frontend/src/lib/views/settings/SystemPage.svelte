<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { describeError } from '../../api/errors';
  import { t, tDynamic } from '../../i18n/index.svelte';
  import { router, stepHash } from '../../router.svelte';
  import { pageRows } from '../../settings/summary';
  import { wizard } from '../../stores/wizard.svelte';
  import InverterVerify from '../../components/InverterVerify.svelte';
  import Icon from '../../ui/Icon.svelte';
  import StepRows from './StepRows.svelte';

  const rows = $derived(pageRows('system', wizard.saved));

  let restarting = $state(false);
  let restartError = $state<string | null>(null);

  /** "Run setup again": the steps count as not done again, the configuration stays. */
  async function rerun(): Promise<void> {
    restarting = true;
    restartError = null;
    try {
      await wizard.restart();
      router.go(stepHash('region'));
    } catch (e) {
      restartError = describeError(e).key;
    } finally {
      restarting = false;
    }
  }
</script>

<div class="card status" class:open={!wizard.completed} data-testid="wizard-status">
  <span class="ico" aria-hidden="true"><Icon name={wizard.completed ? 'ok' : 'info'} /></span>
  <div class="text">
    <p class="name">{t('settings.wizard.title')}</p>
    <p class="hint">{wizard.completed ? t('settings.wizard.done') : t('settings.wizard.open')}</p>
  </div>
  {#if wizard.completed}
    <button
      type="button"
      class="btn secondary small"
      data-testid="open-wizard"
      disabled={restarting}
      onclick={() => void rerun()}>{t('settings.wizard.rerun')}</button
    >
  {:else}
    <a class="btn small" href="#/setup" data-testid="open-wizard">{t('settings.wizard.continue')}</a
    >
  {/if}
</div>
{#if restartError}
  <p class="notice error" role="alert">{tDynamic(restartError, undefined, 'api.error.unknown')}</p>
{/if}

<StepRows {rows} />

<InverterVerify />

<style>
  .status {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--sc-space-3);
    padding-block: var(--sc-space-3);
  }
  .ico {
    display: inline-flex;
    color: var(--sc-ok);
  }
  .open {
    border-color: var(--sc-info);
  }
  .open .ico {
    color: var(--sc-info);
  }
  .text {
    flex: 1 1 14rem;
    min-width: 0;
  }
  .text p {
    margin: 0;
  }
  .name {
    font-weight: var(--sc-weight-strong);
  }
</style>
