<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { t } from '../../i18n/index.svelte';
  import { stepHash } from '../../router.svelte';
  import type { StepRow } from '../../settings/summary';
  import { STEPS, type StepId } from '../../wizard/steps';

  interface Props {
    rows: StepRow[];
  }
  let { rows }: Props = $props();

  const titleOf = (id: StepId): string => t(STEPS.find((x) => x.id === id)!.title);
</script>

<!-- One card, one row per wizard step; the link opens the step in edit mode. -->
<ul class="card rows" data-testid="overview">
  {#each rows as row (row.step)}
    <li data-step={row.step}>
      <div class="text">
        <span class="name">{titleOf(row.step)}</span>
        <span class="hint">{row.text}</span>
      </div>
      <a class="btn secondary small" href={stepHash(row.step)}
        >{row.step === 'summary' ? t('settings.open') : t('action.edit')}<span class="sr-only">
          {titleOf(row.step)}</span
        ></a
      >
    </li>
  {/each}
</ul>

<style>
  .rows {
    margin: 0;
    padding: var(--sc-space-1) var(--sc-space-4);
    list-style: none;
  }
  li {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: var(--sc-space-2) var(--sc-space-4);
    padding: var(--sc-space-3) 0;
  }
  li + li {
    border-top: 1px solid var(--sc-outline);
  }
  .text {
    display: grid;
    flex: 1 1 14rem;
    gap: 2px;
    min-width: 0;
  }
  .name {
    font-weight: var(--sc-weight-strong);
  }
  .hint {
    overflow-wrap: anywhere;
  }
</style>
