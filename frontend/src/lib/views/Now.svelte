<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import DecisionList from '../components/DecisionList.svelte';
  import EnergyFlow from '../components/EnergyFlow.svelte';
  import FlowList from '../components/FlowList.svelte';
  import { t } from '../i18n/index.svelte';
  import { app } from '../stores/app.svelte';
  import { live } from '../stores/live.svelte';

  const configured = $derived(app.site !== null);
  // Until the site config is known assume a battery, so the layout does not jump.
  const hasBattery = $derived(app.site === undefined || app.site?.battery != null);
</script>

<section class="now" aria-labelledby="now-title">
  <h2 id="now-title">{t('now.title')}</h2>

  {#if app.error}
    <p class="notice error" role="alert">{t('app.load_error')}</p>
  {/if}

  {#if !configured}
    <div class="card empty" data-testid="not-configured">
      <p>{t('now.not_configured')}</p>
      <a class="btn" href="#/setup">{t('now.open_settings')}</a>
    </div>
  {:else}
    <div class="card diagram">
      <EnergyFlow flows={live.flows} {hasBattery} />
    </div>
    <FlowList flows={live.flows} {hasBattery} />
  {/if}

  <div class="card">
    <h3>{t('decisions.title')}</h3>
    <DecisionList decisions={live.decisions} />
  </div>
</section>

<style>
  .now {
    display: grid;
    gap: var(--sc-gap);
  }
  .diagram {
    padding: 8px;
  }
  h3 {
    font-size: 1rem;
    margin-bottom: 8px;
  }
  .empty {
    display: grid;
    gap: 12px;
    justify-items: start;
  }
  .empty p {
    margin: 0;
  }
  .btn {
    display: inline-flex;
    align-items: center;
    text-decoration: none;
  }
  .notice.error {
    margin: 0;
    color: var(--error-color);
  }
</style>
