<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount, tick, type Snippet } from 'svelte';
  import { t, tDynamic } from '../i18n/index.svelte';
  import { router, stepHash } from '../router.svelte';
  import { wizard, type SaveState } from '../stores/wizard.svelte';
  import { issueLocText } from '../wizard/issues';
  import Icon from '../ui/Icon.svelte';
  import {
    nextStep,
    prevStep,
    STAGES,
    stageOf,
    STEPS,
    stepIndex,
    type StepId,
  } from '../wizard/steps';

  interface Props {
    step: StepId;
    /** One sentence under the title: what this step is for. */
    intro: string;
    save: SaveState;
    /**
     * Save the step (PUT). Throw on failure; the shell shows the error and stays. When it returns,
     * the step is marked done and the wizard moves on.
     */
    onsave: () => Promise<void> | void;
    /** The step may be left without saving. */
    skippable?: boolean;
    /** Replaces "Save and continue" (summary: "Finish"). */
    nextLabel?: string;
    /** Called instead of moving to the next step (summary). */
    onfinish?: () => Promise<void>;
    /** The primary button is disabled; `blockedHint` says why (shown next to it). */
    blocked?: boolean;
    blockedHint?: string;
    children: Snippet;
  }
  let {
    step,
    intro,
    save,
    onsave,
    skippable = false,
    nextLabel,
    onfinish,
    blocked = false,
    blockedHint,
    children,
  }: Props = $props();

  const index = $derived(stepIndex(step));
  const def = $derived(STEPS[index]!);
  const prev = $derived(prevStep(step));
  const next = $derived(nextStep(step));
  const stage = $derived(stageOf(step));
  const stageIndex = $derived(STAGES.indexOf(stage));
  // The summary counts as done once the whole setup is finished, so a finished wizard
  // reads "10 of 10" and matches "Step n of 10".
  const doneCount = $derived(
    wizard.completedSteps.filter((s) => s !== 'summary').length + (wizard.completed ? 1 : 0),
  );

  let heading: HTMLHeadingElement | undefined = $state();
  let listOpen = $state(false);
  let mounted = false;
  let confirmingLeave = $state(false);

  onMount(() => {
    mounted = true;
    // Closing the tab or reloading would lose unsaved edits: let the browser ask.
    const onUnload = (e: BeforeUnloadEvent): void => {
      if (wizard.dirty().length > 0) e.preventDefault();
    };
    window.addEventListener('beforeunload', onUnload);
    return () => window.removeEventListener('beforeunload', onUnload);
  });

  // Keyboard and screen reader users land on the new step's heading after each navigation.
  $effect(() => {
    void step;
    if (!mounted) return;
    void tick().then(() => heading?.focus());
  });

  async function onNext(): Promise<void> {
    const ok = await save.run(async () => {
      await onsave();
      await wizard.markDone(step);
    });
    if (!ok) return;
    if (onfinish) {
      const finished = await save.run(onfinish);
      if (finished) router.go('#/');
    } else if (next) {
      router.go(stepHash(next));
    }
  }

  function onSkip(): void {
    save.clear();
    if (next) router.go(stepHash(next));
  }

  function onBack(): void {
    save.clear();
    if (prev) router.go(stepHash(prev));
  }

  /** Leaving with unsaved edits asks first; the edits are only kept while the page stays. */
  function leave(): void {
    if (wizard.dirty().length > 0) {
      confirmingLeave = true;
      return;
    }
    router.go('#/settings');
  }

  async function discardAndLeave(): Promise<void> {
    await wizard.discard();
    confirmingLeave = false;
    router.go('#/settings');
  }
</script>

<section class="shell" aria-labelledby="wizard-title" data-testid="wizard" data-step={step}>
  <!-- The four stages (plan phase-7 WP5); the ten steps keep their ids and links below. -->
  <ol class="stages" aria-label={t('wizard.stage.label')} data-testid="wizard-stages">
    {#each STAGES as st, i (st.id)}
      <li
        class:past={i < stageIndex}
        class:current={i === stageIndex}
        aria-current={i === stageIndex ? 'step' : undefined}
        data-stage={st.id}
      >
        <span class="bar" aria-hidden="true"></span>
        <span class="stage-name">{t(st.title)}</span>
      </li>
    {/each}
  </ol>

  <aside class="progress" aria-label={t('wizard.progress.label')}>
    <div class="compact">
      <progress
        max={STEPS.length}
        value={index + 1}
        aria-label={t('wizard.progress.label')}
        aria-valuetext={t('wizard.progress.step', { n: index + 1, total: STEPS.length })}
      ></progress>
      <button
        type="button"
        class="toggle"
        aria-expanded={listOpen}
        aria-controls="wizard-steps"
        onclick={() => (listOpen = !listOpen)}
      >
        <span class="chev" class:open={listOpen}><Icon name="chevron" size={18} /></span>
        {listOpen ? t('wizard.progress.hide') : t('wizard.progress.show')}
      </button>
    </div>
    <div id="wizard-steps" class="steps" class:open={listOpen}>
      {#each STAGES as st (st.id)}
        <p class="kicker group">{t(st.title)}</p>
        <ol>
          {#each st.steps as id (id)}
            {@const done = wizard.completedSteps.includes(id)}
            <li>
              <a
                href={stepHash(id)}
                aria-current={id === step ? 'step' : undefined}
                class:done
                class:current={id === step}
                onclick={() => (listOpen = false)}
              >
                <span class="marker" aria-hidden="true">{done ? '✓' : stepIndex(id) + 1}</span>
                <span class="label">{t(STEPS[stepIndex(id)]!.title)}</span>
                {#if done}<span class="sr-only">{t('wizard.progress.done')}</span>{/if}
              </a>
            </li>
          {/each}
        </ol>
      {/each}
      <p class="done-count">
        {t('wizard.progress.done_count', { done: doneCount, total: STEPS.length })}
      </p>
    </div>
  </aside>

  <div class="body card">
    <header>
      <p class="kicker" data-testid="wizard-count">
        {t('wizard.stage.position', {
          stage: t(stage.title),
          n: index + 1,
          total: STEPS.length,
        })}
      </p>
      <h2 id="wizard-title" tabindex="-1" bind:this={heading}>{t(def.title)}</h2>
      <p class="intro">{intro}</p>
    </header>

    <div class="content">
      {@render children()}
    </div>

    {#if save.error}
      <div class="notice error" role="alert" data-testid="save-error">
        <p>{tDynamic(save.error, undefined, 'api.error.unknown')}</p>
        {#if save.messages.length > 0}
          <ul>
            {#each save.messages as key (key)}
              <li>{t(key)}</li>
            {/each}
          </ul>
        {/if}
        {#if save.issues.length > 0}
          <ul>
            {#each save.issues as issue, i (i)}
              <li>
                <span class="loc">{issueLocText(issue)}</span>
                {tDynamic(issue.key, issue.args, 'api.validation.invalid')}
              </li>
            {/each}
          </ul>
        {/if}
      </div>
    {/if}

    {#if confirmingLeave}
      <div
        class="notice warn"
        role="alertdialog"
        aria-labelledby="leave-msg"
        data-testid="leave-confirm"
      >
        <p id="leave-msg">{t('wizard.leave.unsaved')}</p>
        <div class="confirm-actions">
          <button
            type="button"
            class="btn danger small"
            data-testid="leave-discard"
            onclick={() => void discardAndLeave()}>{t('wizard.leave.discard')}</button
          >
          <button
            type="button"
            class="btn secondary small"
            data-testid="leave-stay"
            onclick={() => (confirmingLeave = false)}>{t('wizard.leave.stay')}</button
          >
        </div>
      </div>
    {/if}

    {#if blocked && blockedHint}
      <p id="blocked-hint-text" class="notice error" role="status" data-testid="blocked-hint">
        {blockedHint}
      </p>
    {/if}

    <!-- One sticky footer on every step: leave at the start, Back, Skip and Next at the end. -->
    <footer class="actions">
      <button type="button" class="btn ghost" onclick={leave}>{t('wizard.leave')}</button>
      <span class="spacer"></span>
      {#if prev}
        <button type="button" class="btn secondary" data-testid="wizard-back" onclick={onBack}
          >{t('action.back')}</button
        >
      {/if}
      {#if skippable && next}
        <button type="button" class="btn ghost" data-testid="wizard-skip" onclick={onSkip}
          >{t('action.skip')}</button
        >
      {/if}
      <button
        type="button"
        class="btn"
        data-testid="wizard-next"
        disabled={save.saving || blocked}
        aria-describedby={blocked && blockedHint ? 'blocked-hint-text' : undefined}
        onclick={onNext}
      >
        {save.saving ? t('action.saving') : (nextLabel ?? t('action.save_continue'))}
      </button>
    </footer>
  </div>
</section>

<style>
  /*
   * Phone and tablet: the stage bar, a compact progress row with the step list behind a toggle,
   * then the step. Desktop (>= 1024 px): the step list as a sticky side column, grouped by stage.
   */
  .shell {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--sc-space-4);
    align-items: start;
  }

  /* --- stages --- */
  .stages {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: var(--sc-space-2);
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .stages li {
    display: grid;
    gap: var(--sc-space-1);
    min-width: 0;
    color: var(--sc-text-muted);
    font-size: var(--sc-font-xs);
    font-weight: var(--sc-weight-strong);
  }
  .bar {
    height: 4px;
    border-radius: var(--sc-radius-pill);
    background: var(--sc-outline-strong);
  }
  .stages .past .bar {
    background: var(--sc-ok);
  }
  .stages .current {
    color: var(--sc-text);
  }
  .stages .current .bar {
    background: var(--sc-accent);
  }
  .stage-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* --- step list --- */
  .progress {
    display: grid;
    gap: var(--sc-space-2);
    min-width: 0;
  }
  .compact {
    display: flex;
    align-items: center;
    gap: var(--sc-space-3);
  }
  progress {
    flex: 1;
    min-width: 0;
    height: 6px;
    accent-color: var(--sc-accent);
  }
  .toggle {
    display: inline-flex;
    align-items: center;
    gap: var(--sc-space-1);
    min-height: var(--sc-target);
    padding: 0 var(--sc-space-2);
    border: 0;
    border-radius: var(--sc-radius-pill);
    background: transparent;
    color: var(--sc-accent);
    font: inherit;
    font-size: var(--sc-font-sm);
    font-weight: var(--sc-weight-strong);
    cursor: pointer;
  }
  .chev {
    display: inline-flex;
    transform: rotate(-90deg);
    transition: transform var(--sc-motion-fast) ease;
  }
  .chev.open {
    transform: none;
  }
  .steps {
    display: none;
    padding: var(--sc-space-2);
    border: 1px solid var(--sc-outline);
    border-radius: var(--sc-radius-lg);
    background: var(--sc-surface);
  }
  .steps.open {
    display: grid;
    gap: var(--sc-space-1);
  }
  .steps ol {
    display: grid;
    gap: 2px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .group {
    padding: var(--sc-space-2) var(--sc-space-2) 0;
  }
  .steps a {
    display: flex;
    align-items: center;
    gap: var(--sc-space-3);
    min-height: var(--sc-target);
    padding: 0 var(--sc-space-2);
    border-radius: var(--sc-radius-md);
    color: var(--sc-text);
    font-size: var(--sc-font-sm);
    text-decoration: none;
  }
  .steps a:hover {
    background: var(--sc-surface-sunken);
  }
  .steps a.current {
    background: var(--sc-accent-bg);
    color: var(--sc-accent);
    font-weight: var(--sc-weight-strong);
  }
  .marker {
    flex: none;
    display: inline-grid;
    place-items: center;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    border: 2px solid var(--sc-outline-strong);
    color: var(--sc-text-muted);
    font-size: var(--sc-font-xs);
    font-weight: var(--sc-weight-strong);
  }
  .steps a.done .marker {
    border-color: var(--sc-ok);
    color: var(--sc-ok);
  }
  /* After .done: the current step is marked as current even when it is done. */
  .steps a.current .marker {
    border-color: var(--sc-accent);
    background: var(--sc-accent);
    color: var(--sc-accent-fg);
  }
  .done-count {
    margin: var(--sc-space-2) var(--sc-space-2) 0;
    color: var(--sc-text-muted);
    font-size: var(--sc-font-xs);
  }

  /* --- the step --- */
  .body {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--sc-space-4);
    min-width: 0;
    padding-bottom: 0;
  }
  header {
    display: grid;
    gap: var(--sc-space-1);
  }
  h2 {
    font-size: var(--sc-font-xl);
  }
  h2:focus {
    outline: none;
  }
  .intro {
    margin: 0;
    max-width: 44rem;
    color: var(--sc-text-muted);
  }
  .content {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--sc-space-4);
    min-width: 0;
  }
  /* Sections inside a step are flat (app.css); the first needs no divider under the intro. */
  .content > :global(.card:first-child) {
    padding-top: 0;
    border-top: 0;
  }
  .loc {
    font-weight: var(--sc-weight-strong);
    margin-inline-end: var(--sc-space-2);
  }
  .confirm-actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sc-space-2);
    margin-top: var(--sc-space-2);
  }
  /* The footer spans the card and stays above the phone's bottom bar while the step scrolls. */
  .actions {
    position: sticky;
    bottom: var(--sc-sticky-bottom, 0px);
    z-index: 2;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--sc-space-2);
    margin: 0 calc(-1 * var(--sc-space-4));
    padding: var(--sc-space-3) var(--sc-space-4);
    border-top: 1px solid var(--sc-outline);
    border-radius: 0 0 var(--sc-radius-lg) var(--sc-radius-lg);
    background: var(--sc-surface);
  }
  .spacer {
    flex: 1;
  }

  @media (min-width: 600px) {
    .actions {
      margin: 0 calc(-1 * var(--sc-space-5));
      padding: var(--sc-space-3) var(--sc-space-5);
    }
  }

  @media (min-width: 1024px) {
    .shell {
      grid-template-columns: 15rem minmax(0, 1fr);
      column-gap: var(--sc-space-6);
    }
    .stages,
    .body {
      grid-column: 2;
    }
    .progress {
      grid-column: 1;
      grid-row: 1 / span 2;
      position: sticky;
      top: calc(var(--sc-sticky-top, 0px) + var(--sc-space-3));
    }
    .compact {
      display: none;
    }
    .steps {
      display: grid;
      gap: var(--sc-space-1);
      padding: 0;
      border: 0;
      background: none;
    }
  }
</style>
