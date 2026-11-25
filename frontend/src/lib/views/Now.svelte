<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import BatteryCard from '../components/BatteryCard.svelte';
  import DecisionList from '../components/DecisionList.svelte';
  import ConfirmResult from '../components/ConfirmResult.svelte';
  import GridBadges from '../components/GridBadges.svelte';
  import DeviceCard from '../components/DeviceCard.svelte';
  import ProposalList from '../components/ProposalList.svelte';
  import PlannedActions from '../components/PlannedActions.svelte';
  import EnergyNow from '../components/EnergyNow.svelte';
  import Card from '../ui/Card.svelte';
  import SectionHeader from '../ui/SectionHeader.svelte';
  import { nameMap } from '../control/present';
  import { onMount, tick } from 'svelte';
  import { t } from '../i18n/index.svelte';
  import { isUsable } from '../flow';
  import { plannedActions, windowIntervals } from '../plan/model';
  import { router } from '../router.svelte';
  import { app } from '../stores/app.svelte';
  import { live } from '../stores/live.svelte';
  import { plan } from '../stores/plan.svelte';

  const configured = $derived(app.site !== null);
  // Until the site config is known assume a battery, so the layout does not jump.
  const hasBattery = $derived(app.site === undefined || app.site?.battery != null);
  const devices = $derived(live.control?.devices ?? []);
  const names = $derived(nameMap(devices, [['battery', t('battery.title')]]));
  const battery = $derived(live.control?.battery ?? null);
  const socPct = $derived(
    isUsable(live.flows?.battery_soc_pct) ? live.flows.battery_soc_pct.value : null,
  );
  const batteryW = $derived(isUsable(live.flows?.battery_w) ? live.flows.battery_w.value : null);

  // The plan feeds the "next planned actions"; it is fetched again when the feed announces one.
  onMount(() => {
    void plan.refresh();
  });
  $effect(() => {
    void live.planUpdates;
    void live.snapshotCount;
    plan.syncWithFeed();
  });
  // `#/?device=<id>` (a link from the decision log) shows and focuses that device's card.
  const wanted = $derived(router.query.get('device'));
  let shown: string | null = null;
  $effect(() => {
    const id = wanted;
    if (!id || id === shown || !devices.some((d) => d.device_id === id)) return;
    shown = id;
    void tick().then(() => {
      const card = document.getElementById(`device-card-${id}`);
      card?.scrollIntoView({ block: 'center' });
      card?.focus({ preventScroll: true });
    });
  });
  const upcoming = $derived(
    plannedActions(windowIntervals(plan.view, 24, new Date(plan.nowMs)), new Date(plan.nowMs), 3),
  );
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
    <GridBadges />
    <EnergyNow flows={live.flows} {hasBattery} site={live.control?.site ?? null} {devices} />
  {/if}

  <ConfirmResult {names} />

  <ProposalList proposals={live.control?.proposals ?? []} {names} />

  {#if battery || (plan.view && !plan.unavailable)}
    <div class="pair">
      {#if battery}
        <BatteryCard {battery} {socPct} powerW={batteryW} />
      {/if}
      {#if plan.view && !plan.unavailable}
        <Card title={t('plan.next.title')} testid="now-next-actions">
          {#snippet actions()}<a href="#/plan">{t('plan.next.open')}</a>{/snippet}
          {#if plan.outdated}
            <p class="notice warn" data-testid="now-plan-outdated">{t('plan.now_outdated')}</p>
          {:else}
            <PlannedActions actions={upcoming} testid="now-planned-actions" />
          {/if}
        </Card>
      {/if}
    </div>
  {/if}

  <section aria-labelledby="devices-title" class="devices">
    <SectionHeader title={t('now.devices')} id="devices-title" />
    {#if devices.length === 0}
      <p class="hint" data-testid="no-devices">{t('now.no_devices')}</p>
    {:else}
      <div class="grid" data-testid="device-cards">
        {#each devices as d (d.device_id)}
          <DeviceCard
            device={d}
            unit={unitOf(d.device_id)}
            config={app.devices?.devices?.find((c) => c.id === d.device_id)}
          />
        {/each}
      </div>
    {/if}
  </section>

  <Card title={t('decisions.title')} testid="now-decisions">
    {#snippet actions()}<a href="#/decisions">{t('decisions.open_log')}</a>{/snippet}
    <DecisionList decisions={live.decisions} {names} />
  </Card>
</section>

<style>
  .now {
    display: grid;
    gap: var(--sc-space-5);
    min-width: 0;
  }
  .pair {
    display: grid;
    gap: var(--sc-space-4);
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr));
  }
  .devices {
    display: grid;
    gap: var(--sc-space-3);
  }
  .devices .grid {
    display: grid;
    gap: var(--sc-space-4);
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 19rem), 1fr));
  }
  .empty {
    display: grid;
    gap: var(--sc-space-3);
    justify-items: start;
  }
  .empty p {
    margin: 0;
  }
  .notice.error {
    margin: 0;
  }
</style>
