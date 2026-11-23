<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../../api/client';
  import { describeError } from '../../api/errors';
  import type { CheckResult } from '../../api/types';
  import FlowList from '../../components/FlowList.svelte';
  import PlanPreview from '../../components/PlanPreview.svelte';
  import WizardShell from '../../components/WizardShell.svelte';
  import { t, tDynamic } from '../../i18n/index.svelte';
  import { live } from '../../stores/live.svelte';
  import { SaveState, wizard } from '../../stores/wizard.svelte';

  const save = new SaveState();

  let result = $state.raw<CheckResult | null>(null);
  let error = $state<string | null>(null);
  let running = $state(false);

  const ICON = { ok: '✓', warn: '▲', error: '✕' } as const;
  const hasBattery = $derived(wizard.saved.site?.battery != null);

  async function run(): Promise<void> {
    running = true;
    try {
      result = await api.check();
      error = null;
    } catch (e) {
      error = describeError(e).key;
    } finally {
      running = false;
    }
  }
  onMount(() => void run());

  async function onsave(): Promise<void> {
    // Nothing to store here; finishing is done by the shell.
  }
  async function onfinish(): Promise<void> {
    await wizard.finish();
  }
</script>

<WizardShell
  step="summary"
  intro={t('summary.intro')}
  {save}
  {onsave}
  {onfinish}
  nextLabel={t('summary.finish')}
  blocked={result?.severity === 'error'}
  blockedHint={t('summary.finish_blocked')}
>
  <section class="card stack" aria-labelledby="chk-title">
    <h3 id="chk-title">{t('summary.check')}</h3>
    {#if error}
      <p class="notice error" role="alert">{tDynamic(error, undefined, 'api.error.unknown')}</p>
    {:else if !result}
      <p class="hint" role="status">{t('summary.checking')}</p>
    {:else}
      <p
        class="overall {result.severity}"
        role="status"
        data-testid="check-overall"
        data-severity={result.severity}
      >
        <span aria-hidden="true">{ICON[result.severity]}</span>
        {t(`summary.overall.${result.severity}`)}
      </p>
      <ul class="items" data-testid="check-items">
        {#each result.items as item, i (i)}
          <li class={item.severity} data-severity={item.severity}>
            <span class="icon" aria-hidden="true">{ICON[item.severity]}</span>
            <span class="sr-only">{t(`summary.severity.${item.severity}`)}</span>
            <span>{tDynamic(item.reason.key, item.reason.args, 'reason.unknown')}</span>
          </li>
        {/each}
      </ul>
    {/if}
    <div>
      <button type="button" class="btn secondary" disabled={running} onclick={() => void run()}
        >{t('summary.recheck')}</button
      >
    </div>
  </section>

  <section class="card stack" aria-labelledby="flow-title">
    <h3 id="flow-title">{t('summary.flows')}</h3>
    <FlowList flows={live.flows} {hasBattery} />
  </section>

  <PlanPreview />
</WizardShell>

<style>
  .items {
    display: grid;
    gap: 6px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .items li {
    display: flex;
    gap: 10px;
    align-items: flex-start;
  }
  .icon {
    flex: none;
    width: 1.5em;
    text-align: center;
    font-weight: 700;
  }
  li.ok .icon,
  .overall.ok {
    color: var(--success-color);
  }
  li.warn .icon,
  .overall.warn {
    color: var(--warning-color);
  }
  li.error .icon,
  .overall.error {
    color: var(--error-color);
  }
  .overall {
    margin: 0;
    font-weight: 700;
  }
</style>
