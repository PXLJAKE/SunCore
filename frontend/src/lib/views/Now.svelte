<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import DecisionList from '../components/DecisionList.svelte';
  import ConfirmResult from '../components/ConfirmResult.svelte';
  import DeviceCard from '../components/DeviceCard.svelte';
  import ProposalList from '../components/ProposalList.svelte';
  import SiteSummary from '../components/SiteSummary.svelte';
  import EnergyFlow from '../components/EnergyFlow.svelte';
  import FlowList from '../components/FlowList.svelte';
  import { nameMap } from '../control/present';
  import { t } from '../i18n/index.svelte';
  import { app } from '../stores/app.svelte';
  import { live } from '../stores/live.svelte';

  const configured = $derived(app.site !== null);
  // Until the site config is known assume a battery, so the layout does not jump.
  const hasBattery = $derived(app.site === undefined || app.site?.battery != null);
  const devices = $derived(live.control?.devices ?? []);
  const names = $derived(nameMap(devices));
  /** Unit of an adjustable load's value, from its configured control. */
  const unitOf = (id: string): string | undefined => {
    const c = app.devices?.devices?.find((d) => d.id === id)?.control;
    return c?.kind === 'continuous' ? c.unit : undefined;
  };
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

  <ConfirmResult {names} />

  <ProposalList proposals={live.control?.proposals ?? []} {names} />

  {#if live.control?.site}<SiteSummary site={live.control.site} {devices} />{/if}

  <section aria-labelledby="devices-title" class="devices">
    <h3 id="devices-title">{t('now.devices')}</h3>
    {#if devices.length === 0}
      <p class="hint" data-testid="no-devices">{t('now.no_devices')}</p>
    {:else}
      <div class="grid" data-testid="device-cards">
        {#each devices as d (d.device_id)}
          <DeviceCard device={d} unit={unitOf(d.device_id)} />
        {/each}
      </div>
    {/if}
  </section>

  <div class="card">
    <div class="head">
      <h3>{t('decisions.title')}</h3>
      <a href="#/decisions">{t('decisions.open_log')}</a>
    </div>
    <DecisionList decisions={live.decisions} {names} />
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
  .devices .grid {
    display: grid;
    gap: var(--sc-gap);
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 19rem), 1fr));
    align-items: start;
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 4px 12px;
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
