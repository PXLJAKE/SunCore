<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount, tick, type Component } from 'svelte';
  import { t, tDynamic, type MessageKey } from '../i18n/index.svelte';
  import { router, settingsHash, type SettingsPage } from '../router.svelte';
  import { stepSummary } from '../settings/summary';
  import { wizard } from '../stores/wizard.svelte';
  import Icon from '../ui/Icon.svelte';
  import type { IconName } from '../ui/icons';
  import AdvancedPage from './settings/AdvancedPage.svelte';
  import DevicesPage from './settings/DevicesPage.svelte';
  import GoalsPage from './settings/GoalsPage.svelte';
  import HaPage from './settings/HaPage.svelte';
  import SystemPage from './settings/SystemPage.svelte';

  /*
   * Settings (plan phase-7 WP5): an index of five groups, one sub-page per group
   * (`#/settings/<page>`). Desktop shows both panes side by side and opens
   * "Your system" for `#/settings`; phones and tablets show the index, then the page alone.
   */
  const uid = $props.id();

  interface PageDef {
    id: SettingsPage;
    icon: IconName;
    title: MessageKey;
    description: MessageKey;
  }
  const PAGES: readonly PageDef[] = [
    {
      id: 'system',
      icon: 'house',
      title: 'settings.page.system',
      description: 'settings.page.system.description',
    },
    {
      id: 'devices',
      icon: 'plug',
      title: 'settings.page.devices',
      description: 'settings.page.devices.description',
    },
    {
      id: 'goals',
      icon: 'target',
      title: 'settings.page.goals',
      description: 'settings.page.goals.description',
    },
    {
      id: 'ha',
      icon: 'bell',
      title: 'settings.page.ha',
      description: 'settings.page.ha.description',
    },
    {
      id: 'advanced',
      icon: 'expert',
      title: 'settings.page.advanced',
      description: 'settings.page.advanced.description',
    },
  ];

  // The pages ride in the Settings chunk, which is lazy as a whole: split further, every page
  // chunk would add its own imports and gzip overhead to the standard path (plan §8, 7-G).
  const VIEWS: Record<SettingsPage, Component> = {
    system: SystemPage,
    devices: DevicesPage,
    goals: GoalsPage,
    ha: HaPage,
    advanced: AdvancedPage,
  };

  const WIDE = '(min-width: 1024px)';
  let wide = $state(typeof window !== 'undefined' && !!window.matchMedia?.(WIDE).matches);

  onMount(() => {
    const media = window.matchMedia?.(WIDE);
    const onChange = (): void => {
      wide = !!media?.matches;
    };
    media?.addEventListener('change', onChange);
    // Reload to pick up changes made elsewhere, but never over unsaved edits of the assistant.
    if (!(wizard.loaded && wizard.dirty().length > 0)) {
      wizard.invalidate();
      void wizard.ensureLoaded();
    }
    return () => media?.removeEventListener('change', onChange);
  });

  /** The page in the pane: the one in the URL, on desktop "Your system" for the index. */
  const page = $derived<SettingsPage | null>(router.page ?? (wide ? 'system' : null));
  const def = $derived(PAGES.find((p) => p.id === page) ?? null);
  const View = $derived(page ? VIEWS[page] : null);
  /** Phones and tablets show either the index or one page. */
  const showIndex = $derived(wide || router.page === null);

  const s = $derived(wizard.saved);
  const hints = $derived<Record<SettingsPage, string>>({
    system: t('settings.page.system.hint'),
    devices: stepSummary('devices', s),
    goals: stepSummary('mode', s),
    ha: t('settings.page.ha.hint'),
    advanced: t('settings.page.advanced.hint'),
  });

  const unsaved = $derived(wizard.dirty().length > 0);

  // Opening a page moves the focus to its heading (the shell only does that between views).
  let heading: HTMLHeadingElement | undefined = $state();
  let lastPage: SettingsPage | null = router.page;
  $effect(() => {
    const current = router.page;
    if (current === lastPage) return;
    lastPage = current;
    if (current) void tick().then(() => heading?.focus());
  });
</script>

<section class="settings" aria-labelledby="{uid}-title">
  {#if showIndex}
    <header class="head">
      <h2 id="{uid}-title">{t('settings.title')}</h2>
      <p class="hint">{t('settings.intro')}</p>
    </header>
  {:else}
    <!-- A page on its own: the view title is the way back to the index. -->
    <h2 id="{uid}-title" class="crumb">
      <a href="#/settings" data-testid="settings-back">
        <Icon name="prev" size={18} />{t('settings.title')}
      </a>
    </h2>
  {/if}

  {#if wizard.loadError}
    <p class="notice error" role="alert">
      {tDynamic(wizard.loadError, undefined, 'api.error.unknown')}
    </p>
  {/if}

  {#if unsaved}
    <div class="notice warn" role="status" data-testid="unsaved-notice">
      <p>{t('settings.unsaved')}</p>
      <div class="row">
        <a class="btn small" href="#/setup">{t('settings.unsaved.resume')}</a>
        <button
          type="button"
          class="btn secondary small"
          data-testid="unsaved-discard"
          onclick={() => void wizard.discard()}>{t('settings.unsaved.discard')}</button
        >
      </div>
    </div>
  {/if}

  <div class="panes" class:wide>
    {#if showIndex}
      <nav class="index" aria-label={t('settings.index_label')} data-testid="settings-index">
        <ul class="card">
          {#each PAGES as p (p.id)}
            <li>
              <a
                href={settingsHash(p.id)}
                aria-current={wide && page === p.id ? 'page' : undefined}
                data-testid="settings-link-{p.id}"
              >
                <span class="ico"><Icon name={p.icon} /></span>
                <span class="text">
                  <span class="name">{t(p.title)}</span>
                  <span class="sub">{hints[p.id]}</span>
                </span>
                <span class="go"><Icon name="next" size={18} /></span>
              </a>
            </li>
          {/each}
        </ul>
      </nav>
    {/if}

    {#if page && def && View}
      <div class="pane" data-testid="settings-page" data-page={page}>
        <header class="pane-head">
          <h3 tabindex="-1" bind:this={heading}>{t(def.title)}</h3>
          <p class="hint">{t(def.description)}</p>
        </header>
        <View />
      </div>
    {/if}
  </div>
</section>

<style>
  .settings {
    display: grid;
    gap: var(--sc-space-4);
  }
  .head {
    display: grid;
    gap: var(--sc-space-1);
  }
  .head .hint {
    max-width: 44rem;
  }
  .crumb {
    font-size: var(--sc-font-sm);
  }
  .crumb a {
    display: inline-flex;
    align-items: center;
    gap: var(--sc-space-1);
    min-height: var(--sc-target);
    padding-inline-end: var(--sc-space-2);
    text-decoration: none;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-2);
    margin-top: var(--sc-space-2);
  }

  .panes {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--sc-space-5);
    align-items: start;
  }
  .panes.wide {
    grid-template-columns: 19rem minmax(0, 1fr);
  }
  .wide .index {
    position: sticky;
    top: calc(var(--sc-sticky-top, 0px) + var(--sc-space-3));
  }

  /* --- the index: one card, a row per group --- */
  .index ul {
    margin: 0;
    padding: var(--sc-space-1) 0;
    list-style: none;
  }
  .index li + li {
    border-top: 1px solid var(--sc-outline);
  }
  .index a {
    display: flex;
    align-items: center;
    gap: var(--sc-space-3);
    min-height: 64px;
    padding: var(--sc-space-2) var(--sc-space-4);
    color: var(--sc-text);
    text-decoration: none;
  }
  .index a:hover {
    background: var(--sc-surface-sunken);
  }
  .index a[aria-current='page'] {
    background: var(--sc-accent-bg);
  }
  .ico {
    display: inline-grid;
    place-items: center;
    flex: none;
    width: 36px;
    height: 36px;
    border-radius: var(--sc-radius-md);
    background: var(--sc-surface-sunken);
    color: var(--sc-text-muted);
  }
  [aria-current='page'] .ico {
    background: var(--sc-accent);
    color: var(--sc-accent-fg);
  }
  .text {
    display: grid;
    flex: 1;
    min-width: 0;
  }
  .name {
    font-weight: var(--sc-weight-strong);
  }
  .sub {
    overflow: hidden;
    color: var(--sc-text-muted);
    font-size: var(--sc-font-sm);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .go {
    display: inline-flex;
    color: var(--sc-text-muted);
  }

  /* --- the page --- */
  .pane {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--sc-space-4);
    min-width: 0;
  }
  .pane-head {
    display: grid;
    gap: var(--sc-space-1);
  }
  .pane-head h3 {
    font-size: var(--sc-font-xl);
  }
  .pane-head h3:focus {
    outline: none;
  }
</style>
