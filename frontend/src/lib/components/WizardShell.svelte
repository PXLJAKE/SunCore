<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onMount, tick, type Snippet } from 'svelte';
  import { t, tDynamic } from '../i18n/index.svelte';
  import { router, stepHash } from '../router.svelte';
  import { wizard, type SaveState } from '../stores/wizard.svelte';
  import { issueLocText } from '../wizard/issues';
  import { nextStep, prevStep, STEPS, stepIndex, type StepId } from '../wizard/steps';

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
  <aside class="progress" aria-label={t('wizard.progress.label')}>
    <div class="compact">
      <p class="count" data-testid="wizard-count">
        {t('wizard.progress.step', { n: index + 1, total: STEPS.length })}
      </p>
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
        {listOpen ? t('wizard.progress.hide') : t('wizard.progress.show')}
      </button>
    </div>
    <ol id="wizard-steps" class="steps" class:open={listOpen}>
      {#each STEPS as s, i (s.id)}
        {@const done = wizard.completedSteps.includes(s.id)}
        <li>
          <a
            href={stepHash(s.id)}
            aria-current={s.id === step ? 'step' : undefined}
            class:done
            class:current={s.id === step}
            onclick={() => (listOpen = false)}
          >
            <span class="marker" aria-hidden="true">{done ? '✓' : i + 1}</span>
            <span class="label">{t(s.title)}</span>
            {#if done}<span class="sr-only">{t('wizard.progress.done')}</span>{/if}
          </a>
        </li>
      {/each}
    </ol>
    <p class="done-count">
      {t('wizard.progress.done_count', { done: doneCount, total: STEPS.length })}
    </p>
  </aside>

  <div class="body card">
    <header>
      <h2 id="wizard-title" tabindex="-1" bind:this={heading}>
        <span class="num">{index + 1}.</span>
        {t(def.title)}
      </h2>
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

    <footer class="actions">
      <button type="button" class="btn secondary" onclick={leave}>{t('wizard.leave')}</button>
      <span class="spacer"></span>
      {#if prev}
        <button type="button" class="btn secondary" data-testid="wizard-back" onclick={onBack}
          >{t('action.back')}</button
        >
      {/if}
      {#if skippable && next}
        <button type="button" class="btn secondary" data-testid="wizard-skip" onclick={onSkip}
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
  .shell {
    display: grid;
    gap: var(--sc-gap);
    align-items: start;
  }
  .progress {
    display: grid;
    gap: 8px;
  }
  .compact {
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    gap: 6px 12px;
  }
  .count {
    margin: 0;
    font-weight: 600;
  }
  progress {
    grid-column: 1 / -1;
    grid-row: 2;
    width: 100%;
    height: 8px;
    accent-color: var(--primary-color);
  }
  .toggle {
    font: inherit;
    min-height: 44px;
    padding: 0 12px;
    background: transparent;
    border: 1px solid var(--divider-color);
    border-radius: 22px;
    color: var(--primary-color);
    cursor: pointer;
  }
  .steps {
    display: none;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .steps.open {
    display: grid;
    gap: 2px;
  }
  .steps a {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 44px;
    padding: 0 10px;
    border-radius: 8px;
    text-decoration: none;
    color: var(--primary-text-color);
  }
  .steps a.current {
    background: color-mix(in srgb, var(--primary-color) 16%, transparent);
    font-weight: 700;
  }
  .marker {
    flex: none;
    display: inline-grid;
    place-items: center;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    border: 2px solid var(--divider-color);
    font-size: 0.8125rem;
    font-weight: 700;
  }
  .steps a.current .marker {
    border-color: var(--primary-color);
    background: var(--primary-color);
    color: var(--text-primary-color);
  }
  .steps a.done .marker {
    border-color: var(--success-color);
    color: var(--success-color);
  }
  .done-count {
    margin: 0;
    color: var(--secondary-text-color);
    font-size: 0.875rem;
  }
  .body {
    display: grid;
    gap: var(--sc-gap);
    min-width: 0;
  }
  header {
    display: grid;
    gap: 6px;
  }
  h2:focus {
    outline: none;
  }
  .num {
    color: var(--primary-color);
  }
  .intro {
    margin: 0;
    color: var(--secondary-text-color);
  }
  .content {
    display: grid;
    gap: var(--sc-gap);
    min-width: 0;
  }
  .notice {
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid currentColor;
  }
  .notice p {
    margin: 0;
  }
  .notice ul {
    margin: 6px 0 0;
    padding-left: 20px;
  }
  .notice.error {
    color: var(--error-color);
  }
  .notice.warn p {
    color: var(--primary-text-color);
  }
  .confirm-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
  }
  .loc {
    font-weight: 600;
    margin-inline-end: 6px;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    position: sticky;
    bottom: 0;
    padding: 10px 0;
    background: var(--card-background-color);
    border-top: 1px solid var(--divider-color);
  }
  .spacer {
    flex: 1;
  }

  @media (min-width: 900px) {
    .shell {
      grid-template-columns: 250px minmax(0, 1fr);
    }
    .progress {
      position: sticky;
      top: 8px;
    }
    .compact {
      display: none;
    }
    .steps {
      display: grid;
      gap: 2px;
    }
  }
</style>
