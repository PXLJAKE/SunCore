<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { i18n, t, type MessageKey } from './lib/i18n/index.svelte';
  import type { OperatingMode } from './lib/api/types';
  import { router } from './lib/router.svelte';
  import { app } from './lib/stores/app.svelte';
  import { live, type LinkStatus } from './lib/stores/live.svelte';
  import EmergencyBanner from './lib/components/EmergencyBanner.svelte';
  import EmergencyStop from './lib/components/EmergencyStop.svelte';
  import Now from './lib/views/Now.svelte';
  import Settings from './lib/views/Settings.svelte';
  import Setup from './lib/views/Setup.svelte';

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

  // Move focus to the page content after navigation (keyboard and screen reader users).
  $effect(() => {
    void router.route;
    if (router.route === 'setup') return; // the wizard moves the focus to its own heading
    if (firstRoute) {
      firstRoute = false;
      return;
    }
    void tick().then(() => main?.focus());
  });
</script>

<button class="skip" type="button" onclick={() => main?.focus()}>{t('nav.skip')}</button>

<header class="top">
  <h1 class="brand">{t('app.title')}</h1>
  <nav aria-label={t('nav.label')}>
    <a href="#/" aria-current={router.route === 'now' ? 'page' : undefined}>{t('nav.now')}</a>
    <a href="#/decisions" aria-current={router.route === 'decisions' ? 'page' : undefined}
      >{t('nav.decisions')}</a
    >
    <a
      href="#/settings"
      aria-current={router.route === 'settings' || router.route === 'setup' ? 'page' : undefined}
      >{t('nav.settings')}</a
    >
  </nav>
  <div class="estop">
    <EmergencyStop />
  </div>
  <div class="status">
    {#if mode}
      <span class="badge mode" class:shadow={mode === 'shadow'} data-testid="mode-badge"
        >{t(MODE[mode])}</span
      >
    {/if}
    <span
      class="conn {live.status}"
      role="status"
      data-testid="connection"
      data-status={live.status}
    >
      <span class="dot" aria-hidden="true"></span>
      {t(STATUS[live.status])}
    </span>
  </div>
</header>

<EmergencyBanner />

{#if app.info?.demo}
  <div class="demo" role="note" data-testid="demo-banner">{t('demo.banner')}</div>
{/if}

<main id="main" tabindex="-1" bind:this={main}>
  {#if router.route === 'setup'}
    <Setup />
  {:else if router.route === 'settings'}
    <Settings />
  {:else if router.route === 'decisions'}
    {#await import('./lib/views/Decisions.svelte') then view}
      <view.default />
    {/await}
  {:else}
    <Now />
  {/if}
</main>

<style>
  .skip {
    position: absolute;
    left: 8px;
    top: -60px;
    z-index: 10;
    min-height: 44px;
    padding: 0 16px;
    border-radius: 8px;
    border: 2px solid var(--primary-color);
    background: var(--card-background-color);
    color: var(--primary-text-color);
    font: inherit;
  }
  .skip:focus {
    top: 8px;
  }
  .top {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 20px;
    padding: 10px 16px;
    background: var(--card-background-color);
    border-bottom: 1px solid var(--divider-color);
  }
  .brand {
    font-size: 1.25rem;
  }
  nav {
    display: flex;
    gap: 4px;
  }
  nav a {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    padding: 0 14px;
    border-radius: 22px;
    text-decoration: none;
    color: var(--primary-text-color);
  }
  nav a[aria-current='page'] {
    background: var(--primary-color);
    color: var(--text-primary-color);
  }
  .estop {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  @media (max-width: 640px) {
    .brand {
      order: 1;
    }
    .estop {
      order: 2;
    }
    nav {
      order: 3;
      width: 100%;
    }
    nav a {
      padding: 0 10px;
    }
    .status {
      order: 4;
      margin-left: 0;
    }
  }
  .status {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px 12px;
  }
  .badge {
    padding: 2px 10px;
    border-radius: 12px;
    border: 1px solid var(--divider-color);
    font-size: 0.875rem;
    font-weight: 600;
  }
  .badge.shadow {
    border-color: var(--info-color);
    color: var(--info-color);
  }
  .conn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 0.875rem;
    color: var(--secondary-text-color);
  }
  .dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--secondary-text-color);
  }
  .conn.live .dot {
    background: var(--success-color);
  }
  .conn.degraded .dot,
  .conn.ha_down .dot {
    background: var(--warning-color);
  }
  .conn.offline .dot {
    background: var(--error-color);
  }
  .conn.degraded,
  .conn.ha_down,
  .conn.offline {
    color: var(--primary-text-color);
    font-weight: 600;
  }
  .demo {
    padding: 8px 16px;
    background: var(--warning-color);
    color: var(--text-primary-color);
    font-weight: 600;
    text-align: center;
  }
  main {
    max-width: 1100px;
    margin: 0 auto;
    padding: var(--sc-gap);
  }
  main:focus {
    outline: none;
  }
</style>
