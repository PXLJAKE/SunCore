<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { paramsApi } from '../api/client';
  import type { Param } from '../api/types';
  import WhatIfForm from '../components/WhatIfForm.svelte';
  import WhatIfResult from '../components/WhatIfResult.svelte';
  import WhatIfRuns from '../components/WhatIfRuns.svelte';
  import { loadParamCatalogue, t, tDynamic } from '../i18n/index.svelte';
  import { expert } from '../stores/expert.svelte';
  import Loading from '../ui/Loading.svelte';
  import SectionHeader from '../ui/SectionHeader.svelte';
  import { WhatIfStore } from '../stores/whatif.svelte';
  import {
    buildRequest,
    emptyForm,
    type FormIssue,
    type WhatIfForm as Form,
  } from '../whatif/model';

  const STATUS = {
    running: 'whatif.status.running',
    done: 'whatif.status.done',
    failed: 'whatif.status.failed',
    cancelled: 'whatif.status.cancelled',
  } as const;
  const uid = $props.id();
  const store = new WhatIfStore();

  let form = $state<Form | null>(null);
  let registry = $state.raw<Param[] | null | false>(null);
  let issues = $state.raw<FormIssue[]>([]);
  /** Announced to screen readers when a run starts or ends (the progress itself is silent). */
  let announce = $state('');

  onMount(() => {
    void store.init().then(() => {
      if (store.today && !form) form = emptyForm(store.today);
    });
    void Promise.all([paramsApi.list(), loadParamCatalogue()]).then(
      ([listing]) => {
        registry = listing.params.filter(
          (p) =>
            p.tags.includes('whatif') && p.available && (p.type === 'float' || p.type === 'int'),
        );
      },
      () => {
        registry = false;
      },
    );
    return () => store.dispose();
  });

  const running = $derived(store.running);
  const detail = $derived(store.detail);
  const pct = $derived(
    running?.progress && running.progress.total > 0
      ? Math.round((running.progress.done / running.progress.total) * 100)
      : 0,
  );

  // A run that was running and is now over is announced once.
  let wasRunning = false;
  $effect(() => {
    if (running) wasRunning = true;
    else if (wasRunning) {
      wasRunning = false;
      const last = store.runs[0];
      announce = last ? t('whatif.announce', { status: t(STATUS[last.status]) }) : '';
    }
  });

  async function submit(): Promise<void> {
    if (!form || !store.today) return;
    const built = buildRequest(form, store.today, registry || []);
    issues = built.issues;
    if (!built.request) return;
    announce = '';
    if (await store.start(built.request)) {
      announce = t('whatif.announce', { status: t(STATUS.running) });
      await tick();
      document.getElementById(`${uid}-progress`)?.scrollIntoView({ block: 'start' });
    }
  }
</script>

<section class="whatif" aria-labelledby="{uid}-title" data-testid="whatif-view">
  <header class="head">
    <h2 id="{uid}-title">{t('whatif.title')}</h2>
    <p class="hint intro">{t('whatif.intro')}</p>
  </header>

  {#if !expert.enabled}
    <p class="card notice info" data-testid="whatif-expert-off">
      {t('whatif.expert_off')}
      <a href="#/settings/advanced">{t('whatif.expert_open')}</a>
    </p>
  {:else if store.unavailable}
    <p class="card notice error" role="alert" data-testid="whatif-unavailable">
      {t('whatif.unavailable')}
    </p>
  {:else if !store.loaded || !form || !store.today}
    <Loading label={t('whatif.loading')} />
    {#if store.error}
      <p class="notice error" role="alert">
        {tDynamic(store.error, undefined, 'api.error.unknown')}
      </p>
    {/if}
  {:else}
    <p class="sr-only" role="status" data-testid="whatif-announce">{announce}</p>

    {#if running}
      <section
        class="card stack progress-card"
        aria-labelledby="{uid}-progress"
        data-testid="whatif-progress"
      >
        <h3 id="{uid}-progress">{t('whatif.progress.title')}</h3>
        <progress
          max="100"
          value={pct}
          aria-label={t('whatif.progress.label')}
          data-testid="whatif-progress-bar">{pct} %</progress
        >
        <p class="hint">
          {running.progress
            ? t('whatif.progress.text', {
                done: running.progress.done,
                total: running.progress.total,
              })
            : t('whatif.progress.starting')}
        </p>
        <div>
          <button
            type="button"
            class="btn secondary"
            data-testid="whatif-cancel"
            onclick={() => void store.remove(running.id)}>{t('action.cancel')}</button
          >
        </div>
      </section>
    {/if}

    {#if store.error}
      <p class="notice error" role="alert" data-testid="whatif-error">
        {tDynamic(store.error, undefined, 'api.error.unknown')}
      </p>
    {/if}

    {#if detail && detail.status === 'done'}
      <WhatIfResult run={detail} />
    {:else if detail && (detail.status === 'failed' || detail.status === 'cancelled')}
      <section class="card stack" data-testid="whatif-stopped" data-status={detail.status}>
        <h3>{t(`whatif.status.${detail.status}` as 'whatif.status.failed')}</h3>
        {#if detail.error}<p>{tDynamic(detail.error.key, detail.error.args)}</p>{/if}
      </section>
    {/if}

    <WhatIfRuns
      runs={store.runs}
      selectedId={store.selectedId}
      onselect={(id) => void store.select(id)}
      onremove={(id) => void store.remove(id)}
    />

    <div id="{uid}-form" class="form">
      <SectionHeader title={t('whatif.form.title')} />
      {#if running}<p class="notice info" data-testid="whatif-busy">{t('whatif.busy')}</p>{/if}
      <WhatIfForm
        bind:form
        params={registry}
        {issues}
        today={store.today}
        busy={store.starting || running !== null}
        onsubmit={() => void submit()}
      />
    </div>
  {/if}
</section>

<style>
  .whatif {
    display: grid;
    gap: var(--sc-space-5);
    min-width: 0;
  }
  .head {
    display: grid;
    gap: var(--sc-space-2);
  }
  .intro {
    max-width: 48rem;
  }
  /* Long single-value fields read badly across a desktop: the form keeps a reading width. */
  .form {
    display: grid;
    gap: var(--sc-space-3);
    min-width: 0;
    max-width: 56rem;
  }
  .progress-card {
    border-color: var(--sc-accent);
  }
  progress {
    width: 100%;
    height: 12px;
    accent-color: var(--sc-accent);
  }
</style>
