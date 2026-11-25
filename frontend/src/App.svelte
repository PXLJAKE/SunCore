<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount, tick, untrack } from 'svelte';
  import { i18n, t, type MessageKey } from './lib/i18n/index.svelte';
  import type { OperatingMode } from './lib/api/types';
  import { destinationOf, router, type Destination } from './lib/router.svelte';
  import { app } from './lib/stores/app.svelte';
  import { control } from './lib/stores/control.svelte';
  import { expert } from './lib/stores/expert.svelte';
  import { live, type LinkStatus } from './lib/stores/live.svelte';
  import EmergencyBanner from './lib/components/EmergencyBanner.svelte';
  import EmergencyStop from './lib/components/EmergencyStop.svelte';
  import Banner from './lib/ui/Banner.svelte';
  import Chip from './lib/ui/Chip.svelte';
  import Icon from './lib/ui/Icon.svelte';
  import Loading from './lib/ui/Loading.svelte';
  import type { IconName } from './lib/ui/icons';
  import Now from './lib/views/Now.svelte';

  let main: HTMLElement | undefined = $state();
  let firstRoute = true;
  let redirected = false;

  const STATUS: Record<LinkStatus, MessageKey> = {
    connecting: 'conn.connecting',
    live: 'conn.live',
    ha_down: 'conn.ha_down',
    degraded: 'conn.degraded',
    offline: 'conn.offline',
  };
  const MODE: Record<OperatingMode, MessageKey> = {
    shadow: 'mode.shadow',
    assisted: 'mode.assisted',
    automatic: 'mode.automatic',
  };

  /** The four destinations (ADR-0019); the hash routes behind them do not change. */
  const DESTINATIONS: readonly {
    id: Destination;
    href: string;
    label: MessageKey;
    icon: IconName;
  }[] = [
    { id: 'now', href: '#/', label: 'nav.now', icon: 'now' },
    { id: 'plan', href: '#/plan', label: 'nav.plan', icon: 'plan' },
    { id: 'analysis', href: '#/history', label: 'nav.analysis', icon: 'analysis' },
    { id: 'settings', href: '#/settings', label: 'nav.settings', icon: 'settings' },
  ];

  const destination = $derived(destinationOf(router.route));

  // Fall back to the decision reasons while /info has not arrived.
  const mode = $derived<OperatingMode | null>(
    live.control?.global_mode ??
      app.info?.mode ??
      (live.decisions.some((d) => d.reasons.some((r) => r.key === 'safety.shadow_mode'))
        ? 'shadow'
        : null),
  );

  onMount(() => {
    const stopRouter = router.listen();
    void app.load();
    // The emergency stop must show even before the first live snapshot.
    void control.refresh();
    live.start();
    return () => {
      stopRouter();
      live.stop();
    };
  });

  $effect(() => {
    document.title = t('app.title');
    document.documentElement.lang = i18n.locale;
  });

  // First start: nobody has completed the wizard, so open it once. Leaving it is allowed and is
  // not undone (no redirect loop); a reload shows the wizard again until it is finished.
  $effect(() => {
    if (redirected || !app.needsSetup) return;
    redirected = true;
    if (router.route === 'now') router.replace('#/setup');
  });

  // Move focus to the page content after navigation (keyboard and screen reader users). Only a
  // change of the route counts: a new query or settings page keeps the focus where the view put
  // it (the derived only notifies when the route itself changes).
  const route = $derived(router.route);
  $effect(() => {
    const current = route;
    if (current === 'setup') return; // the wizard moves the focus to its own heading
    if (firstRoute) {
      firstRoute = false;
      return;
    }
    // A link to a device card (`#/?device=`) focuses that card instead.
    if (current === 'now' && untrack(() => router.query.has('device'))) return;
    void tick().then(() => main?.focus());
  });
</script>

<button class="skip" type="button" onclick={() => main?.focus()}>{t('nav.skip')}</button>

<header class="appbar">
  <h1 class="brand">
    <span class="logo"><Icon name="sun" size={22} /></span>
    <span class="brand-name">{t('app.title')}</span>
  </h1>
  <nav class="destinations" aria-label={t('nav.label')} data-testid="main-nav">
    {#each DESTINATIONS as d (d.id)}
      <a
        href={d.href}
        aria-current={destination === d.id ? 'page' : undefined}
        data-testid="nav-{d.id}"
      >
        <Icon name={d.icon} size={22} />
        <span class="nav-label">{t(d.label)}</span>
      </a>
    {/each}
  </nav>
  <div class="status">
    {#if mode}
      <Chip
        tone={mode === 'shadow' ? 'info' : mode === 'automatic' ? 'ok' : 'accent'}
        testid="mode-badge">{t(MODE[mode])}</Chip
      >
    {/if}
    <span
      class="conn {live.status}"
      role="status"
      data-testid="connection"
      data-status={live.status}
    >
      <span class="dot" aria-hidden="true"></span>
      <span class="conn-text">{t(STATUS[live.status])}</span>
    </span>
  </div>
  <div class="estop">
    <EmergencyStop />
  </div>
</header>

<!-- Banners stack in a fixed order of severity: emergency stop first, then the demo note. -->
<div class="banners">
  <EmergencyBanner />
  {#if app.info?.demo}
    <Banner tone="info" role="note" testid="demo-banner">{t('demo.banner')}</Banner>
  {/if}
</div>

<main id="main" tabindex="-1" bind:this={main}>
  {#if destination === 'analysis'}
    <nav class="subnav" aria-label={t('nav.analysis_label')} data-testid="analysis-tabs">
      <a href="#/history" aria-current={router.route === 'history' ? 'page' : undefined}
        >{t('nav.history')}</a
      >
      <a href="#/decisions" aria-current={router.route === 'decisions' ? 'page' : undefined}
        >{t('nav.decisions')}</a
      >
      {#if expert.enabled || router.route === 'whatif'}
        <a href="#/whatif" aria-current={router.route === 'whatif' ? 'page' : undefined}
          >{t('nav.whatif')}</a
        >
      {/if}
    </nav>
  {/if}
  {#if router.route === 'setup'}
    {#await import('./lib/views/Setup.svelte')}
      <Loading />
    {:then view}
      <view.default />
    {/await}
  {:else if router.route === 'settings'}
    {#await import('./lib/views/Settings.svelte')}
      <Loading />
    {:then view}
      <view.default />
    {/await}
  {:else if router.route === 'plan'}
    {#await import('./lib/views/Plan.svelte') then view}
      <view.default />
    {/await}
  {:else if router.route === 'history'}
    {#await import('./lib/views/History.svelte') then view}
      <view.default />
    {/await}
  {:else if router.route === 'whatif'}
    {#await import('./lib/views/WhatIf.svelte') then view}
      <view.default />
    {/await}
  {:else if router.route === 'decisions'}
    {#await import('./lib/views/Decisions.svelte') then view}
      <view.default />
    {/await}
  {:else}
    <Now />
  {/if}
</main>

<style>
  /*
   * Shell layout (ADR-0019). Phone < 600 px: compact top bar (mark, status, emergency stop) and
   * a fixed bottom bar with the four destinations. Tablet 600-1023 px: top bar with the
   * destinations in a second row. Desktop >= 1024 px: one row. The top bar stays on screen, so
   * the emergency stop is always one tap away.
   */
  :global(:root) {
    --sc-sticky-top: 56px;
    --sc-sticky-bottom: calc(var(--sc-bottombar-h) + env(safe-area-inset-bottom, 0px));
  }
  :global(html) {
    scroll-padding-top: calc(var(--sc-sticky-top) + var(--sc-space-3));
    scroll-padding-bottom: var(--sc-sticky-bottom);
  }

  .skip {
    position: absolute;
    left: var(--sc-space-2);
    top: -60px;
    z-index: 40;
    min-height: var(--sc-target);
    padding: 0 var(--sc-space-4);
    border-radius: var(--sc-radius-pill);
    border: 2px solid var(--sc-accent);
    background: var(--sc-surface);
    color: var(--sc-text);
    font: inherit;
  }
  .skip:focus {
    top: var(--sc-space-2);
  }

  .appbar {
    position: sticky;
    top: 0;
    z-index: 30;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--sc-space-2);
    min-height: 56px;
    padding: var(--sc-space-1) var(--sc-space-4);
    background: var(--sc-surface);
    border-bottom: 1px solid var(--sc-outline);
  }

  .brand {
    display: flex;
    align-items: center;
    gap: var(--sc-space-2);
    font-size: var(--sc-font-lg);
    font-weight: 700;
    letter-spacing: -0.01em;
    white-space: nowrap;
  }
  .logo {
    display: inline-grid;
    place-items: center;
    width: 34px;
    height: 34px;
    border-radius: var(--sc-radius-md);
    background: var(--sc-warn-bg);
    color: var(--sc-energy-pv);
  }

  /* --- destinations: bottom bar on phones --- */
  .destinations {
    position: fixed;
    inset: auto 0 0 0;
    z-index: 30;
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    padding: 0 var(--sc-space-1) env(safe-area-inset-bottom, 0px);
    background: var(--sc-surface);
    border-top: 1px solid var(--sc-outline);
    box-shadow: var(--sc-elev-2);
  }
  .destinations a {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    min-height: var(--sc-bottombar-h);
    min-width: 0;
    padding: var(--sc-space-1) 0;
    color: var(--sc-text-muted);
    font-size: var(--sc-font-xs);
    font-weight: var(--sc-weight-strong);
    text-decoration: none;
    text-align: center;
  }
  .destinations a :global(.icon) {
    width: 48px;
    height: 28px;
    padding: 2px 12px;
    border-radius: var(--sc-radius-pill);
    transition: background-color var(--sc-motion-fast) ease;
  }
  .destinations a[aria-current='page'] {
    color: var(--sc-accent);
  }
  .destinations a[aria-current='page'] :global(.icon) {
    background: var(--sc-accent-bg);
  }
  .nav-label {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* --- status cluster --- */
  .status {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    flex: 1;
    gap: var(--sc-space-2);
    min-width: 0;
  }
  .conn {
    display: inline-flex;
    align-items: center;
    gap: var(--sc-space-2);
    font-size: var(--sc-font-sm);
    color: var(--sc-text-muted);
    white-space: nowrap;
  }
  .dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex: none;
    background: var(--sc-text-muted);
  }
  .conn.live .dot {
    background: var(--sc-ok);
    box-shadow: 0 0 0 3px var(--sc-ok-bg);
  }
  .conn.degraded .dot,
  .conn.ha_down .dot {
    background: var(--sc-warn);
  }
  .conn.offline .dot {
    background: var(--sc-error);
  }
  .conn.degraded,
  .conn.ha_down,
  .conn.offline {
    color: var(--sc-text);
    font-weight: var(--sc-weight-strong);
  }
  /* While live, the dot says enough on small screens; problems are always spelled out. */
  .conn.live .conn-text {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
  }

  .estop {
    display: flex;
    align-items: center;
    gap: var(--sc-space-2);
    flex: none;
  }

  /* --- banners and content --- */
  .banners {
    display: grid;
    gap: var(--sc-space-2);
    max-width: var(--sc-content-max);
    margin: 0 auto;
    padding: var(--sc-space-3) var(--sc-space-4) 0;
  }
  .banners:empty {
    display: none;
  }
  main {
    max-width: var(--sc-content-max);
    margin: 0 auto;
    padding: var(--sc-space-4) var(--sc-space-4) calc(var(--sc-sticky-bottom) + var(--sc-space-5));
  }
  main:focus {
    outline: none;
  }

  /* --- Analysis tabs (History, Decisions, What-if) --- */
  .subnav {
    display: flex;
    gap: var(--sc-space-1);
    margin-bottom: var(--sc-space-4);
    border-bottom: 1px solid var(--sc-outline);
    overflow-x: auto;
    scrollbar-width: none;
  }
  .subnav a {
    display: inline-flex;
    align-items: center;
    min-height: var(--sc-target);
    padding: 0 var(--sc-space-3);
    border-bottom: 3px solid transparent;
    color: var(--sc-text-muted);
    font-size: var(--sc-font-sm);
    font-weight: var(--sc-weight-strong);
    text-decoration: none;
    white-space: nowrap;
  }
  .subnav a:hover {
    color: var(--sc-text);
  }
  .subnav a[aria-current='page'] {
    color: var(--sc-accent);
    border-bottom-color: var(--sc-accent);
  }

  @media (max-width: 599px) {
    .brand-name {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
    }
  }

  /* --- tablet: destinations as a second row of the top bar --- */
  @media (min-width: 600px) {
    :global(:root) {
      --sc-sticky-top: 113px;
      --sc-sticky-bottom: 0px;
    }
    .appbar {
      flex-wrap: wrap;
      column-gap: var(--sc-space-4);
      row-gap: 0;
      min-height: var(--sc-appbar-h);
      padding: 0 var(--sc-space-5);
    }
    .brand {
      min-height: var(--sc-appbar-h);
    }
    .destinations {
      position: static;
      order: 4;
      display: flex;
      flex-basis: 100%;
      gap: var(--sc-space-1);
      padding: 0 0 var(--sc-space-1);
      margin: 0 calc(-1 * var(--sc-space-2));
      border-top: 0;
      box-shadow: none;
      background: transparent;
    }
    .destinations a {
      flex-direction: row;
      gap: var(--sc-space-2);
      min-height: var(--sc-target);
      padding: 0 var(--sc-space-3);
      border-radius: var(--sc-radius-pill);
      font-size: var(--sc-font-sm);
    }
    .destinations a :global(.icon) {
      width: 20px;
      height: 20px;
      padding: 0;
      background: none !important;
    }
    .destinations a:hover {
      color: var(--sc-text);
      background: var(--sc-surface-sunken);
    }
    .destinations a[aria-current='page'] {
      background: var(--sc-accent-bg);
      color: var(--sc-accent);
    }
    .status {
      gap: var(--sc-space-3);
    }
    .banners {
      padding: var(--sc-space-4) var(--sc-space-5) 0;
    }
    main {
      padding: var(--sc-space-5) var(--sc-space-5) var(--sc-space-7);
    }
  }

  /* --- desktop: one row --- */
  @media (min-width: 1024px) {
    :global(:root) {
      --sc-sticky-top: var(--sc-appbar-h);
    }
    .appbar {
      flex-wrap: nowrap;
      padding: 0 var(--sc-space-6);
    }
    .destinations {
      order: 0;
      flex-basis: auto;
      margin: 0 0 0 var(--sc-space-4);
      padding: 0;
    }
    .conn.live .conn-text {
      position: static;
      width: auto;
      height: auto;
      overflow: visible;
      clip: auto;
    }
    .banners,
    main {
      padding-left: var(--sc-space-6);
      padding-right: var(--sc-space-6);
    }
  }
</style>
